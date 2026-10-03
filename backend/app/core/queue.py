"""Job enqueue facade: `await enqueue(kind, payload)` -> Job id. Backed by the jobs table + arq/redis
(app.workers.queue). With AEO_QUEUE_INLINE=1 (tests/dev without a worker) the handler runs in-process."""

import os
import uuid
from typing import Any

import structlog

from app.core.db import get_sessionmaker
from app.models.core import Job

log = structlog.get_logger()


class QueueUnavailable(RuntimeError):
    pass


def inline_enabled() -> bool:
    return os.environ.get("AEO_QUEUE_INLINE", "").lower() in {"1", "true", "yes"}


async def enqueue(kind: str, payload: dict[str, Any] | None = None) -> uuid.UUID:
    payload = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in (payload or {}).items()}
    if inline_enabled():
        return await _run_inline(kind, payload)
    try:
        from app.workers import queue as wq
    except ImportError as exc:
        raise QueueUnavailable(f"worker queue module unavailable: {exc}") from exc
    try:
        return await wq.enqueue_job(kind, payload)
    except wq.QueueUnavailable as exc:
        raise QueueUnavailable(str(exc)) from exc


async def _run_inline(kind: str, payload: dict[str, Any]) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        job = Job(kind=kind, status="running", payload=payload, attempts=1)
        session.add(job)
        await session.commit()
        try:
            from app.workers.jobs import HANDLERS

            result = await HANDLERS[kind](session, payload)
            job.status, job.result = "success", result
        except Exception as exc:  # recorded on the row, never swallowed
            await session.rollback()
            log.exception("job.inline_failed", kind=kind)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
        await session.commit()
        return job.id
