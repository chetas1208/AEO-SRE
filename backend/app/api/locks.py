"""Per-entity mutation locks for the API.

A Postgres advisory lock held on a DEDICATED connection for the whole request, so it survives the commits the request
makes along the way (approval -> ledger -> package). Two concurrent approve/reject/execute/verify calls on the same
entity are serialized; the second then sees the committed outcome and returns it (retry-safe). No-op on SQLite.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.api.errors import Conflict
from app.core.db import get_engine

LOCK_WAIT_SECONDS = 30.0


def _key(kind: str, ident: uuid.UUID | str) -> int:
    raw = uuid.uuid5(uuid.NAMESPACE_URL, f"aeo-sre:{kind}:{ident}").bytes[:8]
    return int.from_bytes(raw, "big", signed=True)


@asynccontextmanager
async def entity_lock(kind: str, ident: uuid.UUID | str, *, wait: float = LOCK_WAIT_SECONDS):
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        yield
        return
    key = _key(kind, ident)
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        deadline = time.monotonic() + wait
        while not (await conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": key})).scalar():
            if time.monotonic() > deadline:
                raise Conflict(f"another request is changing this {kind}; retry shortly",
                               {"kind": kind, "retryable": True}, code="CONCURRENT_MODIFICATION")
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            try:
                await conn.execute(text("select pg_advisory_unlock(:k)"), {"k": key})
            except Exception:  # noqa: BLE001 - closing the connection releases the session lock anyway
                pass
