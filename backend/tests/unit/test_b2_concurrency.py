"""B2: concurrent detect/ingest never crash; unique indexes make losers 'duplicate, skip' (Postgres)."""

from __future__ import annotations

import asyncio

from app.incidents.detector import detect_incidents
from app.models.core import Incident, Signal
from app.services.ingestion import run_scheduled_ingest
from sqlalchemy import func, select

from tests import factories as f
from tests.unit.test_profound_client import NOW, _org, make_client, mock_ingest_http


async def test_concurrent_detect_creates_one_incident(session, sessionmaker):
    org = await f.make_org(session)
    cl = await f.make_prompt_cluster(session, org)
    await f.make_signal_series(session, org, values=f.visibility_drop_values(), cluster=cl)

    async def run():
        async with sessionmaker() as s:
            made = await detect_incidents(s, org.id, now=f.NOW)
            await s.commit()
            return len(made)

    counts = await asyncio.gather(*(run() for _ in range(4)))
    assert sum(counts) == 1
    n = (await session.execute(select(func.count()).select_from(Incident).where(Incident.org_id == org.id))).scalar()
    assert n == 1


async def test_concurrent_ingest_never_crashes_and_never_duplicates(session, sessionmaker, mock_http):
    org = await _org(session)
    mock_ingest_http(mock_http)

    async def run():
        client, _ = make_client()
        async with sessionmaker() as s:
            res = await run_scheduled_ingest(s, org.id, client=client, now=NOW)
            await s.commit()
        await client.aclose()
        return res

    results = await asyncio.gather(*(run() for _ in range(3)))
    assert all(r.status != "failed" for r in results), [r.error for r in results]
    rows = (await session.execute(select(Signal.idempotency_key).where(Signal.org_id == org.id))).scalars().all()
    assert rows and len(rows) == len(set(rows))
