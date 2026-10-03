"""Outbox write/claim/retry/lag helpers. Postgres stays the system of record; none of this needs Neo4j.

Write side: `enqueue_graph_event(session, event_type, aggregate_type, aggregate_id, payload, organization_id)` inserts the
row in the caller's transaction (idempotent on the deterministic id). The ORM hook in `app/graph/events.py` calls the same
insert for every domain mutation, so domain code normally never calls it directly.
Claim side: `claim_batch` uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent projector workers never take the same row.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import utcnow
from app.graph import model as gm
from app.models.graph_outbox import GraphOutbox

log = structlog.get_logger()
CHECKPOINT_KEY = "graph_projection_checkpoint"


@dataclass
class EventSpec:
    """One outbox row to write: `payload` is the projection plan (nodes / rels / optional event)."""

    event_type: str
    aggregate_type: str
    aggregate_id: str
    version: str
    organization_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    @property
    def id(self) -> uuid.UUID:
        return gm.event_id(self.aggregate_type, self.aggregate_id, self.event_type, self.version)

    def row(self) -> dict[str, Any]:
        now = self.created_at or utcnow()
        return {
            "id": self.id, "event_type": self.event_type, "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id), "organization_id": str(self.organization_id),
            "payload": self.payload, "schema_version": gm.PLAN_VERSION, "created_at": now, "attempts": 0,
            "next_attempt_at": now,
        }


def _insert_stmt(dialect_name: str, values: dict[str, Any]):
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(GraphOutbox.__table__).values(**values).on_conflict_do_nothing(index_elements=["id"])


def insert_outbox_sync(connection, spec: EventSpec) -> None:
    """Used inside the ORM flush hook (sync Connection of the same transaction)."""
    connection.execute(_insert_stmt(connection.dialect.name, spec.row()))


async def enqueue_graph_event(session: AsyncSession, event_type: str, aggregate_type: str, aggregate_id: Any,
                              payload: dict[str, Any], organization_id: Any, *, version: str | None = None) -> uuid.UUID:
    """Insert the outbox row in the SAME transaction as the caller's domain mutation (flush/commit decide together).
    Idempotent: the id is uuid5(aggregate_type, aggregate_id, event_type, version); a second call is a no-op."""
    if not get_settings().graph_outbox_enabled:
        return gm.event_id(aggregate_type, aggregate_id, event_type, version or "disabled")
    ver = version or hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]  # noqa: S324
    spec = EventSpec(event_type, aggregate_type, str(aggregate_id), ver, str(organization_id), payload)
    conn = await session.connection()
    await session.execute(_insert_stmt(conn.dialect.name, spec.row()))
    return spec.id


# ---------------------------------------------------------------------------------------------------------------
async def claim_batch(session: AsyncSession, limit: int | None = None, *, now: datetime | None = None) -> list[GraphOutbox]:
    """Lock up to `limit` due pending rows (SKIP LOCKED). The locks last until the caller commits/rolls back."""
    lim = limit or get_settings().graph_outbox_batch
    stmt = (select(GraphOutbox)
            .where(GraphOutbox.processed_at.is_(None), GraphOutbox.dead_at.is_(None),
                   GraphOutbox.next_attempt_at <= (now or utcnow()))
            .order_by(GraphOutbox.created_at, GraphOutbox.id).limit(lim).with_for_update(skip_locked=True))
    return list((await session.execute(stmt)).scalars().all())


def backoff_seconds(attempts: int) -> float:
    s = get_settings()
    return min(s.graph_outbox_backoff_max_s, s.graph_outbox_backoff_base_s * (2 ** max(0, attempts - 1)))


def mark_done(rows: list[GraphOutbox], now: datetime | None = None) -> None:
    t = now or utcnow()
    for r in rows:
        r.processed_at, r.last_error = t, None


def mark_failed(row: GraphOutbox, error: str, *, outage: bool, now: datetime | None = None) -> None:
    """Outage (graph unreachable): keep retrying, never count toward dead-lettering. Poison row: count the attempt,
    back off, dead-letter after `graph_outbox_max_attempts`."""
    t = now or utcnow()
    row_err = error[:2000]
    row.last_error = row_err
    if outage:
        row.next_attempt_at = t + timedelta(seconds=backoff_seconds(max(1, row.attempts + 1)))
        return
    row.attempts = (row.attempts or 0) + 1
    if row.attempts >= get_settings().graph_outbox_max_attempts:
        row.dead_at = t
        log.error("graph.outbox_dead_lettered", outbox_id=str(row.id), event_type=row.event_type, attempts=row.attempts)
    else:
        row.next_attempt_at = t + timedelta(seconds=backoff_seconds(row.attempts))


# ---------------------------------------------------------------------------------------------------------------
async def lag(session: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    """Projection lag: backlog (pending, not dead-lettered), oldest unprocessed age, dead letters, last error."""
    t = now or utcnow()
    pending = (GraphOutbox.processed_at.is_(None), GraphOutbox.dead_at.is_(None))
    backlog, oldest = (await session.execute(
        select(func.count(), func.min(GraphOutbox.created_at)).where(*pending))).one()
    dead = (await session.execute(select(func.count()).where(GraphOutbox.dead_at.is_not(None),
                                                            GraphOutbox.processed_at.is_(None)))).scalar_one()
    last_err = (await session.execute(select(GraphOutbox.last_error).where(*pending, GraphOutbox.last_error.is_not(None))
                                      .order_by(GraphOutbox.created_at).limit(1))).scalar()
    age = None
    if oldest is not None:
        oldest_utc = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
        age = max(0.0, (t - oldest_utc).total_seconds())
    stale_s = get_settings().graph_stale_seconds
    return {"backlog": int(backlog or 0), "oldest_unprocessed_age_s": None if age is None else round(age, 1),
            "dead_lettered": int(dead or 0), "last_error": last_err, "stale_threshold_s": stale_s,
            "stale": bool(age is not None and age > stale_s)}


async def is_graph_stale(session: AsyncSession | None = None) -> bool:
    """True when the oldest unprocessed outbox row is older than GRAPH_STALE_SECONDS (default 300): graph-derived
    context may lag the system of record and the policy must fall back to the BaselinePolicy (`graph_context_stale`).
    An empty backlog is fresh. Needs only Postgres (never Neo4j)."""
    if session is not None:
        return bool((await lag(session))["stale"])
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        return bool((await lag(s))["stale"])


async def graph_lag(session: AsyncSession | None = None) -> dict[str, Any]:
    """Health/capabilities block: lag + checkpoint (see `projector.projector_state`)."""
    from app.core.db import get_sessionmaker
    from app.models.core import Setting

    async def _go(s: AsyncSession) -> dict[str, Any]:
        out = await lag(s)
        cp = await s.get(Setting, CHECKPOINT_KEY)
        out["checkpoint"] = dict(cp.value) if cp else None
        return out

    if session is not None:
        return await _go(session)
    async with get_sessionmaker()() as s:
        return await _go(s)
