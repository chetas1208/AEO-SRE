"""Outbox: atomicity with the domain transaction, SKIP LOCKED, retry/backoff/dead-letter, immutability, lag."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from app.core.db import utcnow
from app.graph import outbox
from app.graph.outbox import EventSpec, claim_batch, enqueue_graph_event, is_graph_stale, lag, mark_failed
from app.models.core import Organization
from app.models.graph_outbox import GraphOutbox, ImmutableOutboxError
from sqlalchemy import func, select, update

from tests import factories as f
from tests.conftest import USE_PG


async def count(session) -> int:
    return (await session.execute(select(func.count()).select_from(GraphOutbox))).scalar_one()


async def test_outbox_row_is_written_in_the_domain_transaction(session):
    org = Organization(name="Atomic", domain="atomic.example")
    session.add(org)
    await session.flush()
    assert await count(session) == 1  # visible inside the transaction
    await session.rollback()
    assert await count(session) == 0  # rolled-back change leaves no event


async def test_commit_keeps_exactly_one_row_per_change(session):
    org = await f.make_org(session)
    rows = (await session.execute(select(GraphOutbox))).scalars().all()
    assert [r.event_type for r in rows] == ["organization.upserted"]
    assert rows[0].aggregate_id == str(org.id) and rows[0].organization_id == str(org.id)
    assert rows[0].processed_at is None and rows[0].attempts == 0 and rows[0].schema_version == 1


async def test_rollback_after_incident_leaves_no_rows_for_it(session):
    org = await f.make_org(session)
    base = await count(session)
    from app.models.core import Incident
    inc = Incident(org_id=org.id, title="x", detected_at=utcnow())
    session.add(inc)
    await session.flush()
    assert await count(session) == base + 2  # incident.changed + incident.created
    await session.rollback()
    assert await count(session) == base


async def test_enqueue_helper_is_idempotent_and_transactional(session):
    org_id = uuid.uuid4()
    payload = {"v": 1, "nodes": [], "rels": [], "event": None}
    a = await enqueue_graph_event(session, "custom.thing", "organization", org_id, payload, org_id, version="1")
    b = await enqueue_graph_event(session, "custom.thing", "organization", org_id, payload, org_id, version="1")
    assert a == b
    assert await count(session) == 1
    await session.rollback()
    assert await count(session) == 0


async def test_duplicate_deterministic_id_is_skipped_not_duplicated(session):
    spec = EventSpec("x.y", "organization", "agg-1", "v1", "org-1", {"v": 1})
    assert spec.id == EventSpec("x.y", "organization", "agg-1", "v1", "org-1", {"v": 1}).id
    assert spec.id != EventSpec("x.y", "organization", "agg-1", "v2", "org-1", {"v": 1}).id


async def test_processing_fields_only_are_mutable(session):
    await f.make_org(session)
    row = (await session.execute(select(GraphOutbox))).scalars().one()
    row.attempts = 3
    row.last_error = "boom"
    await session.commit()  # allowed
    row.payload = {"v": 1, "tampered": True}
    with pytest.raises(ImmutableOutboxError):
        await session.commit()
    await session.rollback()
    await session.delete(row)
    with pytest.raises(ImmutableOutboxError):
        await session.commit()
    await session.rollback()


@pytest.mark.skipif(not USE_PG, reason="SKIP LOCKED needs PostgreSQL")
async def test_skip_locked_two_workers_never_claim_the_same_row(sessionmaker):
    async with sessionmaker() as s:
        for i in range(10):
            s.add(GraphOutbox(id=uuid.uuid4(), event_type="t", aggregate_type="organization", aggregate_id=str(i),
                              organization_id="o", payload={"v": 1}, next_attempt_at=utcnow() - timedelta(seconds=1),
                              created_at=utcnow() + timedelta(milliseconds=i)))
        await s.commit()
    async with sessionmaker() as s1, sessionmaker() as s2:
        a = await claim_batch(s1, 5)  # s1 keeps its row locks (transaction open)
        b = await claim_batch(s2, 10)
        assert len(a) == 5 and len(b) == 5
        assert {r.id for r in a}.isdisjoint({r.id for r in b})
        await s1.rollback()
        await s2.rollback()
    async with sessionmaker() as s3:  # released locks: everything is claimable again
        assert len(await claim_batch(s3, 20)) == 10


async def _row(session, **kw) -> GraphOutbox:
    r = GraphOutbox(id=uuid.uuid4(), event_type="t", aggregate_type="organization", aggregate_id="1", organization_id="o",
                    payload={"v": 1}, next_attempt_at=utcnow(), **kw)
    session.add(r)
    await session.commit()
    return r


async def test_retry_backoff_grows_and_dead_letters_after_max_attempts(session, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GRAPH_OUTBOX_MAX_ATTEMPTS", "4")
    get_settings.cache_clear()
    r = await _row(session)
    now = utcnow()
    waits = []
    for i in range(3):
        mark_failed(r, "bad cypher", outage=False, now=now)
        waits.append((r.next_attempt_at - now).total_seconds())
        assert r.dead_at is None and r.attempts == i + 1
    assert waits == [5.0, 10.0, 20.0]
    mark_failed(r, "bad cypher", outage=False, now=now)
    assert r.attempts == 4 and r.dead_at is not None
    await session.commit()
    assert await claim_batch(session, 10, now=now + timedelta(days=1)) == []  # dead letters are never claimed again


async def test_outage_never_counts_toward_dead_lettering(session):
    r = await _row(session)
    now = utcnow()
    for _ in range(50):
        mark_failed(r, "ServiceUnavailable", outage=True, now=now)
    assert r.attempts == 0 and r.dead_at is None and r.next_attempt_at > now
    assert outbox.backoff_seconds(1000) <= 900.0  # capped


async def test_not_due_rows_are_not_claimed(session):
    r = await _row(session)
    r.next_attempt_at = utcnow() + timedelta(minutes=5)
    await session.commit()
    assert await claim_batch(session, 10) == []
    assert len(await claim_batch(session, 10, now=utcnow() + timedelta(minutes=6))) == 1


async def test_lag_and_staleness(session):
    assert (await lag(session))["backlog"] == 0 and await is_graph_stale(session) is False  # empty backlog is fresh
    fresh = await _row(session)
    assert (await lag(session))["stale"] is False
    await session.execute(update(GraphOutbox).where(GraphOutbox.id == fresh.id)
                          .values(created_at=utcnow() - timedelta(seconds=301)))
    await session.commit()
    info = await lag(session)
    assert info["backlog"] == 1 and info["oldest_unprocessed_age_s"] >= 300 and info["stale"] is True
    assert await is_graph_stale(session) is True
    await session.execute(update(GraphOutbox).where(GraphOutbox.id == fresh.id).values(processed_at=utcnow()))
    await session.commit()
    assert (await lag(session))["backlog"] == 0 and await is_graph_stale(session) is False
