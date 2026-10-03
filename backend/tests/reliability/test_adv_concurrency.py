"""Concurrency on Postgres: N parallel callers racing the same mutation must produce exactly ONE effect."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import app.core.db as appdb
import pytest
from app.domain.enums import ExperimentStatus
from app.incidents.detector import detect_incidents
from app.learning.ingest import IngestError, NotRewardable, ingest_reward
from app.models.core import Incident
from app.models.interventions import Approval, Execution, Experiment, Reward
from app.models.policy import PolicyVersion
from app.services import pipeline
from sqlalchemy import func, select

from tests import factories as f
from tests.conftest import USE_PG
from tests.reliability.helpers import add_obs, executed_experiment, proposed_intervention

pytestmark = pytest.mark.skipif(not USE_PG, reason="row-level locking needs Postgres")
H = {"X-Actor": "alice@testco.example"}
N = 6


async def race(*coros, timeout: float = 45.0):
    """gather with a hang detector: a deadlock fails the test (with the stuck stacks) instead of hanging the suite."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    if pending:
        stacks = []
        for t in pending:
            stacks.append(repr(t.get_stack(limit=4)[-2:]))
            t.cancel()
        raise AssertionError(f"DEADLOCK/HANG: {len(pending)} of {len(tasks)} calls never finished\n" + "\n".join(stacks))
    return [t.result() for t in tasks]


async def n_rows(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def test_parallel_approvals_yield_one_approval_one_package(app_client, session, org):
    _, iv, exp = await proposed_intervention(session, org)
    rs = await race(*[app_client.post(f"/api/interventions/{iv.id}/approve", headers=H) for _ in range(N)])
    assert {r.status_code for r in rs} <= {200, 409}, [(r.status_code, r.text[:200]) for r in rs]
    assert any(r.status_code == 200 for r in rs)
    assert await n_rows(session, Approval) == 1
    assert await n_rows(session, Execution) == 1
    e = await session.get(Experiment, exp.id, populate_existing=True)
    assert e.status == ExperimentStatus.APPROVED and e.executed_at is None


async def test_parallel_approve_and_reject_cannot_both_win(app_client, session, org):
    _, iv, _ = await proposed_intervention(session, org)
    calls = [app_client.post(f"/api/interventions/{iv.id}/{v}", headers=H) for v in ("approve", "reject") * 3]
    rs = await race(*calls)
    assert all(r.status_code in (200, 409) for r in rs), [(r.status_code, r.text[:200]) for r in rs]
    rows = (await session.execute(select(Approval))).scalars().all()
    assert len(rows) == 1 and rows[0].status.value in ("approved", "rejected")
    wins = {r.url.path.rsplit("/", 1)[-1] for r in rs if r.status_code == 200}
    assert wins == {"approve"} if rows[0].status.value == "approved" else wins == {"reject"}


async def test_parallel_reward_ingestion_writes_one_reward_and_one_policy_version(session, org):
    now = datetime.now(UTC)
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1))

    async def attempt():
        async with appdb.get_sessionmaker()() as s:
            try:
                await ingest_reward(s, exp_id)
                await s.commit()
                return "ok"
            except (IngestError, NotRewardable) as e:
                await s.rollback()
                return type(e).__name__
            except Exception as e:  # noqa: BLE001 - a DB-level uniqueness loss is acceptable, a duplicate row is not
                await s.rollback()
                return type(e).__name__

    out = await race(*[attempt() for _ in range(N)])
    assert out.count("ok") == 1, out
    assert await n_rows(session, Reward) == 1
    assert (await session.execute(select(func.count()).select_from(PolicyVersion).where(
        PolicyVersion.source_experiment_id == exp_id))).scalar() == 1
    assert await n_rows(session, PolicyVersion) == 2


async def test_parallel_verify_jobs_reward_once(session, org):
    now = datetime.now(UTC)
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await f.make_signal(session, org, value=55.0, observed_at=now - timedelta(hours=1))

    async def run():
        async with appdb.get_sessionmaker()() as s:
            try:
                return (await pipeline.verify(s, exp_id))["status"]
            except Exception as e:  # noqa: BLE001
                await s.rollback()
                return type(e).__name__

    out = await race(*[run() for _ in range(4)])
    assert await n_rows(session, Reward) == 1, out
    assert await n_rows(session, PolicyVersion) == 2, out
    e = await session.get(Experiment, exp_id, populate_existing=True)
    assert e.status == ExperimentStatus.REWARDED


async def test_parallel_detection_opens_one_incident(session, org):
    await f.make_signal_series(session, org, metric="visibility", values=f.visibility_drop_values())

    async def run():
        async with appdb.get_sessionmaker()() as s:
            try:
                created = await detect_incidents(s, org.id, now=f.NOW)
                await s.commit()
                return len(created)
            except Exception as e:  # noqa: BLE001
                await s.rollback()
                return type(e).__name__

    out = await race(*[run() for _ in range(4)])
    assert await n_rows(session, Incident) == 1, out
