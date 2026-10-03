"""`graph_projected` SSE events for the global feed (GET /api/events).

The projector runs in the worker process, so the API cannot be notified in-process. Instead each SSE stream polls the
Postgres outbox (cheap, indexed) for rows projected since its watermark and emits ONE coalesced event per
organization per tick: {type, organization_id, projected, last_event_id, last_event_type, backlog, time}. The tick
interval is the rate limit (`MIN_INTERVAL_S`). Needs only Postgres; Neo4j state is irrelevant to the signal.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select

log = structlog.get_logger()
MIN_INTERVAL_S = 2.0


class Coalescer:
    """Pure accumulator: add(org, event_id, type) many times, drain() once per tick -> one payload per org."""

    def __init__(self) -> None:
        self._acc: dict[str, dict[str, Any]] = {}

    def add(self, organization_id: str, event_id: str | None, event_type: str | None, n: int = 1) -> None:
        a = self._acc.setdefault(organization_id, {"projected": 0, "last_event_id": None, "last_event_type": None})
        a["projected"] += n
        if event_id is not None:
            a["last_event_id"], a["last_event_type"] = event_id, event_type

    def drain(self, backlog: int = 0, now: datetime | None = None) -> list[dict[str, Any]]:
        t = (now or datetime.now(UTC)).isoformat()
        out = [{"type": "graph_projected", "organization_id": org, "backlog": backlog, "time": t, **a}
               for org, a in sorted(self._acc.items())]
        self._acc = {}
        return out


async def poll_projected(since: datetime, *, sessionmaker: Any = None) -> tuple[list[tuple[str, str, str, datetime]], int, datetime]:
    """Rows projected after `since` (id, type, org, processed_at; capped), pending backlog, new watermark."""
    from app.core.db import get_sessionmaker
    from app.models.graph_outbox import GraphOutbox

    sm = sessionmaker or get_sessionmaker()
    async with sm() as s:
        rows = (await s.execute(
            select(GraphOutbox.id, GraphOutbox.event_type, GraphOutbox.organization_id, GraphOutbox.processed_at)
            .where(GraphOutbox.processed_at > since).order_by(GraphOutbox.processed_at).limit(5000))).all()
        backlog = (await s.execute(select(func.count()).select_from(GraphOutbox).where(
            GraphOutbox.processed_at.is_(None), GraphOutbox.dead_at.is_(None)))).scalar_one()
    mark = rows[-1][3] if rows else since
    return [(str(r[0]), r[1], r[2], r[3]) for r in rows], int(backlog), mark


async def graph_projected_events(*, interval_s: float = MIN_INTERVAL_S, sessionmaker: Any = None,
                                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> AsyncIterator[dict[str, Any]]:
    """Yields coalesced `graph_projected` dicts forever. Errors in the poll are swallowed (the feed must not die)."""
    watermark = now()
    co = Coalescer()
    while True:
        await asyncio.sleep(interval_s)
        try:
            rows, backlog, watermark = await poll_projected(watermark, sessionmaker=sessionmaker)
        except Exception as exc:  # noqa: BLE001 - DB hiccup / table missing: try again next tick
            log.info("graph_sse.poll_failed", error=type(exc).__name__)
            continue
        for eid, etype, org, _at in rows:
            co.add(org, eid, etype)
        for ev in co.drain(backlog=backlog, now=now()):
            yield ev


async def merge_streams(primary: AsyncIterator[dict[str, Any]], secondary: AsyncIterator[dict[str, Any]]
                        ) -> AsyncIterator[dict[str, Any]]:
    """Interleave two async iterators; closing this generator cancels both pumps."""
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    done = object()

    async def pump(it: AsyncIterator[dict[str, Any]], critical: bool) -> None:
        try:
            async for item in it:
                await q.put(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("graph_sse.pump_failed", error=type(exc).__name__)
        finally:
            if critical:
                await q.put(done)

    tasks = [asyncio.create_task(pump(primary, True)), asyncio.create_task(pump(secondary, False))]
    try:
        while True:
            item = await q.get()
            if item is done:
                return
            yield item
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for it in (primary, secondary):
            aclose = getattr(it, "aclose", None)
            if aclose:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass
