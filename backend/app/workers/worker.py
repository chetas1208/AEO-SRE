"""arq worker. Run: `cd backend && uv run arq app.workers.worker.WorkerSettings`.

Every job is persisted in the `jobs` table (queued -> running -> success|failed), retried with exponential
backoff on transient errors, and failures are stored on the row, never swallowed.
"""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import structlog
from arq import Retry, cron
from arq.worker import Worker  # noqa: F401  (re-export for tests)
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_engine, get_sessionmaker, utcnow
from app.models.core import Job, Organization
from app.services.pipeline import PermanentError
from app.workers.jobs import HANDLERS
from app.workers.queue import close_pool, enqueue_job, redis_settings

log = structlog.get_logger()

MAX_TRIES = 3
BACKOFF_SECONDS = (15, 60, 240)
# Profound refreshes tracked prompts daily. Four pulls restate the recent window without polling every half hour.
INGEST_HOURS = (1, 7, 13, 19)
INGEST_MINUTE = 15


def next_ingest_at(now: datetime | None = None) -> datetime:
    """Next scheduled Profound ingest, UTC. Creating an organization still ingests immediately."""
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    slots: list[datetime] = []
    for day in (0, 1):
        base = (now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
        for hour in INGEST_HOURS:
            slot = base.replace(hour=hour, minute=INGEST_MINUTE)
            if slot > now:
                slots.append(slot)
    return min(slots)


async def run_tracked(ctx: dict[str, Any], kind: str, job_id: str) -> dict[str, Any] | None:
    handler = HANDLERS[kind]
    attempt = int(ctx.get("job_try", 1))
    structlog.contextvars.bind_contextvars(job_id=job_id, job_kind=kind, attempt=attempt)
    try:
        return await _run_tracked(handler, kind, job_id, attempt)
    finally:
        structlog.contextvars.unbind_contextvars("job_id", "job_kind", "attempt")


async def _run_tracked(handler, kind: str, job_id: str, attempt: int) -> dict[str, Any] | None:
    async with get_sessionmaker()() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            log.error("job.missing_row", kind=kind, job_id=job_id)
            return None
        job.status, job.attempts, job.error = "running", attempt, None
        await session.commit()
        payload = dict(job.payload or {})
        try:
            result = await handler(session, payload)
        except PermanentError as exc:
            await session.rollback()
            return await _finish(session, job, "failed", error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            await session.rollback()
            log.exception("job.failed", kind=kind, job_id=job_id, attempt=attempt)
            err = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_TRIES:
                await _finish(session, job, "queued", error=f"attempt {attempt} failed, retrying: {err}")
                raise Retry(defer=BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]) from exc
            return await _finish(session, job, "failed", error=err)
        return await _finish(session, job, "success", result=result)


async def _finish(session, job: Job, status: str, *, result=None, error: str | None = None):
    job.status, job.error = status, error
    if result is not None:
        job.result = result
    job.updated_at = utcnow()
    await session.commit()
    log.info("job.done", kind=job.kind, job_id=str(job.id), status=status, error=error)
    return result


STALE_RUNNING_AFTER = timedelta(minutes=30)


async def reap_stale_jobs(older_than: timedelta = STALE_RUNNING_AFTER) -> int:
    """A worker killed mid-job leaves its row `running` forever. arq redelivers the job while it can; a row nobody has
    touched for `older_than` is marked failed so the interruption is visible instead of looking like progress."""
    cutoff = utcnow() - older_than
    async with get_sessionmaker()() as session:
        stale = (await session.execute(
            select(Job).where(Job.status == "running", Job.updated_at < cutoff))).scalars().all()
        for job in stale:
            job.status = "failed"
            job.error = f"interrupted: worker stopped before finishing (no progress since {job.updated_at.isoformat()})"
            job.updated_at = utcnow()
            log.warning("job.reaped", kind=job.kind, job_id=str(job.id), attempts=job.attempts)
        await session.commit()
        return len(stale)


async def cron_reap(ctx: dict[str, Any]) -> int:
    return await reap_stale_jobs()


def _arq_fn(kind: str):
    async def fn(ctx: dict[str, Any], job_id: str):
        return await run_tracked(ctx, kind, job_id)

    fn.__name__ = kind
    fn.__qualname__ = kind
    return fn


JOB_FUNCTIONS = [_arq_fn(k) for k in HANDLERS]


# ---- periodic fan-out: one Job row per org so one org's failure never blocks the others ----
async def _fan_out(kind: str) -> int:
    async with get_sessionmaker()() as session:
        org_ids = (await session.execute(select(Organization.id))).scalars().all()
        for oid in org_ids:
            await enqueue_job(kind, {"org_id": str(oid)}, session=session)
    return len(org_ids)


async def cron_ingest(ctx: dict[str, Any]) -> int:
    return await _fan_out("ingest_profound_signals")


async def cron_detect(ctx: dict[str, Any]) -> int:
    return await _fan_out("detect_incidents")


async def cron_discovery_gap(ctx: dict[str, Any]) -> int:
    return await _fan_out("detect_discovery_gaps")


async def cron_mixpanel_ingest(ctx: dict[str, Any]) -> int:
    from app.integrations.mixpanel.auth import mixpanel_configured

    if not mixpanel_configured(get_settings()):
        return 0
    return await _fan_out("ingest_mixpanel_events")


async def cron_verify(ctx: dict[str, Any]) -> int:
    """Enqueue verification for experiments whose observation window has opened and are not yet verified."""
    from app.services.pipeline import due_experiment_ids

    async with get_sessionmaker()() as session:
        ids = await due_experiment_ids(session)
        for eid in ids:
            await enqueue_job("verify_experiment", {"experiment_id": str(eid)}, session=session)
    return len(ids)


async def cron_graph_project(ctx: dict[str, Any]) -> dict[str, Any]:
    """Drain the Neo4j projection outbox (idempotent; SKIP LOCKED so several workers never double-process).
    Never raises: a down graph leaves the rows pending and the domain untouched."""
    from app.graph.projector import drain

    try:
        return await drain(limit=1000, time_budget_s=45)
    except Exception:  # noqa: BLE001
        log.exception("graph.projection_failed")
        return {"state": "DEGRADED", "error": "projector crashed (see logs)"}


async def on_startup(ctx: dict[str, Any]) -> None:
    from app.api.logging import configure_logging

    cfg = get_settings()
    configure_logging(cfg.log_level, json_logs=cfg.environment == "production")
    log.info("worker.startup", env=get_settings().environment)
    from app.graph import startup_graph

    await startup_graph()  # never raises: a down graph must not stop the worker


async def on_shutdown(ctx: dict[str, Any]) -> None:
    from app.graph import close_graph_client

    await close_graph_client()
    await close_pool()
    await get_engine().dispose()


class WorkerSettings:
    functions = JOB_FUNCTIONS
    cron_jobs: ClassVar[list] = [
        cron(cron_ingest, hour={1, 7, 13, 19}, minute=15, run_at_startup=False, unique=True),
        cron(cron_detect, hour={1, 7, 13, 19}, minute=25, run_at_startup=False, unique=True),
        cron(cron_discovery_gap, hour={3, 15}, minute=40, run_at_startup=False, unique=True),
        cron(cron_verify, minute={10, 25, 40, 55}, run_at_startup=False, unique=True),
        cron(cron_reap, minute={5, 35}, run_at_startup=True, unique=True),
        cron(cron_graph_project, run_at_startup=True, unique=True),  # every minute
        cron(cron_mixpanel_ingest, minute=set(range(60)), run_at_startup=False, unique=True),
    ]
    redis_settings = redis_settings()
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_tries = MAX_TRIES
    job_timeout = 900
    keep_result = 3600
    health_check_interval = 30
