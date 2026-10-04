"""Job enqueue helper: persists a Job row, then hands it to arq. Single entry point for API + pipeline."""
import uuid
from datetime import timedelta
from typing import Any

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models.core import Job

log = structlog.get_logger()

# job kind -> arq function name. Kinds are stored in Job.kind.
JOB_KINDS = (
    "ingest_profound_signals",
    "detect_incidents",
    "investigate_incident",
    "fetch_external_evidence",
    "rank_evidence",
    "generate_hypotheses",
    "score_interventions",
    "execute_intervention",
    "verify_experiment",
    "calculate_reward",
    "update_policy",
    "detect_discovery_gaps",
    "ingest_mixpanel_events",
    "profound_agent_generation",
    "sync_profound_agent_runs",
)

_pool: ArqRedis | None = None


class QueueUnavailable(RuntimeError):
    pass


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_job(
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    session: AsyncSession | None = None,
    defer_by: timedelta | None = None,
) -> uuid.UUID:
    """Create a queued Job row and push it to arq. Returns the Job id.

    Raises QueueUnavailable (after recording the failure on the Job row) if Redis is unreachable.
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    own = session is None
    sess = session or get_sessionmaker()()
    try:
        job = Job(kind=kind, status="queued", payload=_jsonable(payload or {}), attempts=0)
        sess.add(job)
        await sess.commit()
        try:
            pool = await get_pool()
            await pool.enqueue_job(kind, str(job.id), _job_id=str(job.id), _defer_by=defer_by)
        except Exception as exc:  # redis down: record, never pretend it will run
            job.status = "failed"
            job.error = f"enqueue failed: {exc!r}"
            await sess.commit()
            log.error("job.enqueue_failed", kind=kind, job_id=str(job.id), error=repr(exc))
            raise QueueUnavailable(job.error) from exc
        log.info("job.enqueued", kind=kind, job_id=str(job.id))
        return job.id
    finally:
        if own:
            await sess.close()


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in d.items()}
