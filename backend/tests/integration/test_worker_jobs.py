"""Worker job tracking (A14): persisted state, retries with backoff, failures recorded, permanent errors not retried."""
import uuid

import pytest
from app.models.core import Job
from app.services import pipeline
from app.workers import worker
from app.workers.jobs import HANDLERS
from arq import Retry
from sqlalchemy import select


async def make_job(session, kind="update_policy", payload=None) -> Job:
    job = Job(kind=kind, status="queued", payload=payload if payload is not None else {"experiment_id": str(uuid.uuid4())})
    session.add(job)
    await session.commit()
    return job


async def reload(session, job_id) -> Job:
    return (await session.execute(select(Job).where(Job.id == job_id).execution_options(populate_existing=True))).scalar_one()


async def test_success_is_persisted(session, monkeypatch):
    async def ok(_session, payload):
        return {"done": True}

    monkeypatch.setitem(HANDLERS, "update_policy", ok)
    job = await make_job(session)
    assert await worker.run_tracked({"job_try": 1}, "update_policy", str(job.id)) == {"done": True}
    row = await reload(session, job.id)
    assert (row.status, row.attempts, row.error, row.result) == ("success", 1, None, {"done": True})


async def test_transient_failure_retries_then_fails_recorded(session, monkeypatch):
    async def boom(_session, payload):
        raise RuntimeError("redis hiccup")

    monkeypatch.setitem(HANDLERS, "update_policy", boom)
    job = await make_job(session)
    with pytest.raises(Retry):
        await worker.run_tracked({"job_try": 1}, "update_policy", str(job.id))
    row = await reload(session, job.id)
    assert row.status == "queued" and "retrying" in row.error and row.attempts == 1
    await worker.run_tracked({"job_try": worker.MAX_TRIES}, "update_policy", str(job.id))
    row = await reload(session, job.id)
    assert row.status == "failed" and "RuntimeError: redis hiccup" in row.error


async def test_permanent_error_never_retries(session, monkeypatch):
    async def bad(_session, payload):
        raise pipeline.PermanentError("approval missing")

    monkeypatch.setitem(HANDLERS, "update_policy", bad)
    job = await make_job(session)
    await worker.run_tracked({"job_try": 1}, "update_policy", str(job.id))
    row = await reload(session, job.id)
    assert row.status == "failed" and "approval missing" in row.error


async def test_bad_payload_is_permanent(session):
    job = await make_job(session, payload={})
    await worker.run_tracked({"job_try": 1}, "update_policy", str(job.id))
    row = await reload(session, job.id)
    assert row.status == "failed" and "experiment_id" in row.error


def test_every_job_kind_has_handler_and_arq_function():
    from app.workers.queue import JOB_KINDS

    assert set(JOB_KINDS) == set(HANDLERS)
    assert {f.__name__ for f in worker.JOB_FUNCTIONS} == set(JOB_KINDS)
    assert len(worker.WorkerSettings.cron_jobs) == 5  # ingest, detect, discovery-gap, verify, reap-stale-jobs
