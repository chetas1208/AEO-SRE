"""Projection-lag check. N2's `is_graph_stale()` is used when it exists; otherwise a local check on the Postgres
`graph_outbox` backlog (oldest unprocessed row older than the threshold => stale).

Never raises. If the lag cannot be determined, the graph is reported stale (policy falls back to BaselinePolicy)."""
from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog

log = structlog.get_logger()
DEFAULT_MAX_LAG_S = 120.0
_N2_MODULES = ("app.graph.projector", "app.graph.projection", "app.graph.outbox", "app.graph.health")

# (stale, reason). reason None when fresh.
StalenessCheck = Callable[[str], Awaitable[tuple[bool, str | None]]]


async def _n2_check(organization_id: str) -> tuple[bool, str | None] | None:
    for mod_name in _N2_MODULES:
        try:
            fn = getattr(importlib.import_module(mod_name), "is_graph_stale", None)
        except Exception:  # noqa: BLE001 - module absent or failing to import
            continue
        if fn is None:
            continue
        try:
            res = fn()
            if inspect.isawaitable(res):
                res = await res
        except Exception as exc:  # noqa: BLE001
            return True, f"is_graph_stale_failed:{type(exc).__name__}"
        if isinstance(res, tuple):
            return bool(res[0]), (str(res[1]) if len(res) > 1 and res[1] else None)
        return bool(res), ("projection_lag" if res else None)
    return None


async def _local_outbox_check(max_lag_s: float) -> tuple[bool, str | None]:
    try:
        from sqlalchemy import text

        from app.core.db import get_sessionmaker

        async with get_sessionmaker()() as session:
            row = (await session.execute(text(
                "SELECT count(*) AS n, min(created_at) AS oldest FROM graph_outbox WHERE processed_at IS NULL"
            ))).one()
        n, oldest = int(row[0] or 0), row[1]
        if n == 0 or oldest is None:
            return False, None
        if isinstance(oldest, str):
            oldest = datetime.fromisoformat(oldest)
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        lag = (datetime.now(UTC) - oldest).total_seconds()
        return (lag > max_lag_s, f"projection_lag_{int(lag)}s_backlog_{n}" if lag > max_lag_s else None)
    except Exception as exc:  # noqa: BLE001 - table missing / db down: unknown => treat as stale
        log.info("graph.staleness_unknown", error=type(exc).__name__)
        return True, "projection_lag_unknown"


async def default_staleness_check(organization_id: str, max_lag_s: float = DEFAULT_MAX_LAG_S) -> tuple[bool, str | None]:
    n2 = await _n2_check(organization_id)
    if n2 is not None:
        return n2
    return await _local_outbox_check(max_lag_s)
