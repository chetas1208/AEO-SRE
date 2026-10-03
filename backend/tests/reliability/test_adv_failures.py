"""Failure injection: crashes, transaction failures, dependency loss. Nothing may be left half-done or reported as done."""
from __future__ import annotations

import httpx
import pytest
from app.domain.enums import ExperimentStatus, IncidentState
from app.models.core import Incident, Job
from app.models.interventions import Approval, Execution, Experiment
from app.services import pipeline
from app.workers import worker
from app.workers.jobs import HANDLERS
from sqlalchemy import func, select

from tests import factories as f
from tests.reliability.helpers import proposed_intervention

H = {"X-Actor": "alice@testco.example"}


async def n(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def reload(session, model, ident):
    return await session.get(model, ident, populate_existing=True)


# ---- transaction failure midway through approval -> experiment activation ----------------------------------------


async def test_ledger_failure_during_approval_rolls_everything_back(app_client, session, org, monkeypatch):
    inc, iv, exp = await proposed_intervention(session, org)
    from app.experiments import ledger

    real = ledger.attach_approval

    async def explode_midway(sess, experiment, approval=None, **kw):
        await real(sess, experiment, approval, **kw)  # state mutated + flushed ...
        raise RuntimeError("connection reset by peer")  # ... then the transaction dies

    monkeypatch.setattr(ledger, "attach_approval", explode_midway)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_client._transport.app, raise_app_exceptions=False),
                               base_url="http://t")
    r = await client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    await client.aclose()
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert await n(session, Approval) == 0, "no approval may survive a failed activation transaction"
    assert await n(session, Execution) == 0
    e = await reload(session, Experiment, exp.id)
    assert e.status == ExperimentStatus.PROPOSED and e.approval_id is None and e.approver is None
    assert (await reload(session, Incident, inc.id)).state == IncidentState.AWAITING_APPROVAL.value


async def test_activation_failure_after_a_durable_decision_is_reported_and_retry_completes_it(app_client, session,
                                                                                              org, monkeypatch):
    _, iv, exp = await proposed_intervention(session, org)
    real = pipeline.execute
    calls = {"n": 0}

    async def flaky(sess, iv_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return await real(sess, iv_id, **kw)

    monkeypatch.setattr(pipeline, "execute", flaky)
    first = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert first.status_code == 200 and "could not be activated" in (first.json()["message"] or "")
    assert await n(session, Execution) == 0, "nothing is claimed as activated"
    again = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert again.status_code == 200 and again.json()["message"] is None, again.text
    assert await n(session, Approval) == 1 and await n(session, Execution) == 1
    assert (await reload(session, Experiment, exp.id)).status == ExperimentStatus.APPROVED


# ---- worker restart / crash mid-job -------------------------------------------------------------------------------


async def make_job(session, kind="update_policy", **kw) -> Job:
    job = Job(kind=kind, status=kw.pop("status", "queued"), payload={"experiment_id": "00000000-0000-0000-0000-000000000000"},
              **kw)
    session.add(job)
    await session.commit()
    return job


async def test_job_orphaned_in_running_is_rerun_on_redelivery_and_never_left_silently_partial(session, monkeypatch):
    """Worker killed mid-job: the row says 'running'. arq redelivers (job_try 2): it must run again to a final state."""
    seen = []

    async def handler(_s, payload):
        seen.append(1)
        return {"done": True}

    monkeypatch.setitem(HANDLERS, "update_policy", handler)
    job = await make_job(session, status="running", attempts=1)
    out = await worker.run_tracked({"job_try": 2}, "update_policy", str(job.id))
    row = await reload(session, Job, job.id)
    assert out == {"done": True} and row.status == "success" and row.attempts == 2 and seen == [1]


async def test_crash_exactly_at_the_end_of_a_handler_is_visible_as_failed_not_success(session, monkeypatch):
    async def handler(_s, payload):
        raise ConnectionResetError("worker lost the DB mid-flight")

    monkeypatch.setitem(HANDLERS, "update_policy", handler)
    job = await make_job(session)
    await worker.run_tracked({"job_try": worker.MAX_TRIES}, "update_policy", str(job.id))
    row = await reload(session, Job, job.id)
    assert row.status == "failed" and "ConnectionResetError" in row.error and row.result is None


async def test_stale_running_jobs_are_reaped_to_failed_visibly(session):
    from datetime import timedelta

    from app.core.db import utcnow
    from app.workers.worker import reap_stale_jobs

    old = await make_job(session, status="running", attempts=1)
    fresh = await make_job(session, status="running", attempts=1)
    old.updated_at = utcnow() - timedelta(hours=3)
    await session.commit()
    reaped = await reap_stale_jobs(older_than=timedelta(minutes=30))
    assert reaped == 1
    assert (await reload(session, Job, old.id)).status == "failed"
    assert "interrupted" in (await reload(session, Job, old.id)).error
    assert (await reload(session, Job, fresh.id)).status == "running"


# ---- dependency loss ----------------------------------------------------------------------------------------------


async def test_redis_loss_degrades_api_but_database_stays_consistent(app_client, session, org, no_queue):
    inc = await f.make_incident(session, org, state=IncidentState.DETECTED)
    h = await app_client.get("/api/health")
    assert h.status_code == 200 and h.json()["status"] == "degraded" and h.json()["database"] == "healthy"
    r = await app_client.post(f"/api/incidents/{inc.id}/investigate", headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    fresh = await reload(session, Incident, inc.id)
    assert fresh.state == IncidentState.DETECTED.value and fresh.investigation_status == "not_started"
    assert await n(session, Job) == 0
    assert (await app_client.get("/api/incidents")).status_code == 200, "reads keep working without Redis"
    assert (await app_client.get("/api/experiments")).status_code == 200


async def test_db_failure_inside_a_job_is_retried_then_failed_with_a_useful_error(session, monkeypatch):
    from sqlalchemy.exc import OperationalError

    async def handler(_s, payload):
        raise OperationalError("select 1", {}, Exception("server closed the connection unexpectedly"))

    monkeypatch.setitem(HANDLERS, "update_policy", handler)
    job = await make_job(session)
    from arq import Retry

    with pytest.raises(Retry):
        await worker.run_tracked({"job_try": 1}, "update_policy", str(job.id))
    assert (await reload(session, Job, job.id)).status == "queued"
    await worker.run_tracked({"job_try": worker.MAX_TRIES}, "update_policy", str(job.id))
    row = await reload(session, Job, job.id)
    assert row.status == "failed" and "OperationalError" in row.error
