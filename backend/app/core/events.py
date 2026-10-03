"""Incident event bus: persist IncidentEvent, fan out to SSE (Redis pub/sub + in-process fallback)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker, utcnow
from app.core.event_types import bounded_metadata, canonical_event_type
from app.domain.enums import StepStatus
from app.models.core import IncidentEvent

log = structlog.get_logger()
CHANNEL = "aeo:incident_events:{incident_id}"
GLOBAL_PATTERN = "aeo:incident_events:*"
TERMINAL_STATUSES = {StepStatus.SUCCESS.value, StepStatus.FAILED.value}


def event_to_dict(row: IncidentEvent) -> dict[str, Any]:
    at = row.at if isinstance(row.at, datetime) else utcnow()
    return {
        "id": str(row.id),
        "seq": row.seq,
        "incident_id": str(row.incident_id),
        "timestamp": at.isoformat(),
        "stage": row.stage,
        "event_type": canonical_event_type(row.stage, row.status, row.metadata_ or {}),
        "status": row.status,
        "message": row.message,
        "metadata": bounded_metadata(row.metadata_),
    }


QUEUE_MAX = 2000  # per-subscriber buffer; a slower consumer is cut off and resumes from the DB via Last-Event-ID


def _offer(queue: asyncio.Queue, item: Any) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        queue.overflow = True  # type: ignore[attr-defined]


class EventBus:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url if redis_url is not None else get_settings().redis_url
        self._redis = None
        self._redis_ok: bool | None = None
        self._local: dict[str, set[asyncio.Queue]] = {}
        self._global: set[asyncio.Queue] = set()
        self.last_redis_error: str | None = None

    async def _get_redis(self):
        if self._redis_ok is False:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                client = aioredis.from_url(self._redis_url, decode_responses=True)
                await client.ping()
                self._redis, self._redis_ok = client, True
            except Exception as exc:  # redis unavailable -> in-process fan-out only
                self._redis_ok, self.last_redis_error = False, str(exc)
                log.warning("event_bus.redis_unavailable", error=str(exc))
                return None
        return self._redis

    async def redis_available(self) -> bool:
        return await self._get_redis() is not None

    async def emit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """emit(session_or_none, incident_id, stage, status, message="", metadata=None)
        or emit(incident_id, stage, status, message="", metadata=None).
        Persists the event (own session when none supplied; a supplied session is committed so
        subscribers can see the row) and publishes it."""
        if args and (args[0] is None or isinstance(args[0], AsyncSession)):
            session_or_none, args = args[0], args[1:]
        else:
            session_or_none = kwargs.pop("session", None)
        return await self._emit(session_or_none, *args, **kwargs)

    async def _emit(
        self,
        session_or_none: AsyncSession | None,
        incident_id: uuid.UUID | str,
        stage: str,
        status: StepStatus | str,
        message: str = "",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        incident_uuid = incident_id if isinstance(incident_id, uuid.UUID) else uuid.UUID(str(incident_id))
        row = IncidentEvent(
            id=uuid.uuid4(),
            incident_id=incident_uuid,
            stage=stage,
            status=str(status.value if isinstance(status, StepStatus) else status),
            message=message,
            metadata_=metadata or {},
            at=utcnow(),
        )
        if session_or_none is not None:
            session_or_none.add(row)
            await session_or_none.flush()
            payload = event_to_dict(row)
            await session_or_none.commit()  # subscribers replay from DB; commit before publishing
        else:
            async with get_sessionmaker()() as session:
                session.add(row)
                await session.flush()
                payload = event_to_dict(row)
                await session.commit()
        await self._publish(str(incident_uuid), payload)
        return payload

    async def _publish(self, incident_id: str, payload: dict[str, Any]) -> None:
        for queue in list(self._local.get(incident_id, ())):
            _offer(queue, payload)
        for queue in list(self._global):
            _offer(queue, payload)
        client = await self._get_redis()
        if client is not None:
            try:
                await client.publish(CHANNEL.format(incident_id=incident_id), json.dumps(payload))
            except Exception as exc:
                log.warning("event_bus.publish_failed", error=str(exc))

    async def replay(self, incident_id: uuid.UUID, after_seq: int | None = None) -> list[dict[str, Any]]:
        async with get_sessionmaker()() as session:
            stmt = select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
            if after_seq is not None:
                stmt = stmt.where(IncidentEvent.seq > after_seq)
            rows = (await session.execute(stmt.order_by(IncidentEvent.seq))).scalars().all()
            return [event_to_dict(r) for r in rows]

    async def subscribe(
        self,
        incident_id: uuid.UUID | str,
        last_event_id: str | int | None = None,
        heartbeat_seconds: float = 15.0,
        stop_on_idle: bool = False,
    ) -> AsyncIterator[dict[str, Any] | None]:
        """Yield persisted events after `last_event_id` (an event seq), then live ones.
        Yields None as a heartbeat tick. Local queue gives low-latency delivery in this process;
        events from other processes arrive via Redis pub/sub."""
        incident_uuid = incident_id if isinstance(incident_id, uuid.UUID) else uuid.UUID(str(incident_id))
        key = str(incident_uuid)
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._local.setdefault(key, set()).add(queue)
        pubsub = None
        pump: asyncio.Task | None = None
        try:
            client = await self._get_redis()
            if client is not None:
                pubsub = client.pubsub()
                await pubsub.subscribe(CHANNEL.format(incident_id=key))

                async def _pump() -> None:
                    async for msg in pubsub.listen():
                        if msg.get("type") == "message":
                            try:
                                _offer(queue, json.loads(msg["data"]))
                            except Exception:
                                continue

                pump = asyncio.create_task(_pump())

            try:
                after = int(last_event_id) if last_event_id not in (None, "") else None
            except (TypeError, ValueError):
                after = None
            seen = after if after is not None else -1
            for ev in await self.replay(incident_uuid, after):
                seen = max(seen, ev["seq"])
                yield ev
            while True:
                try:
                    if getattr(queue, "overflow", False):
                        log.warning("event_bus.subscriber_overflow", incident_id=key)
                        return  # client reconnects with Last-Event-ID and replays from the DB
                    ev = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except TimeoutError:
                    if stop_on_idle:
                        return
                    yield None
                    continue
                # redis + local delivery may duplicate; seq makes it idempotent
                if ev["seq"] <= seen:
                    continue
                seen = ev["seq"]
                yield ev
        finally:
            self._local.get(key, set()).discard(queue)
            if pump:
                pump.cancel()
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.aclose()
                except Exception:
                    pass

    async def subscribe_global(self, heartbeat_seconds: float = 10.0) -> AsyncIterator[dict[str, Any]]:
        """All incident events across incidents, plus {"type": "heartbeat"} ticks (no replay).
        Items are {"type": "heartbeat"|"incident_event", ...}."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._global.add(queue)
        pubsub = pump = None
        seen: dict[str, None] = {}
        try:
            client = await self._get_redis()
            if client is not None:
                pubsub = client.pubsub()
                await pubsub.psubscribe(GLOBAL_PATTERN)

                async def _pump() -> None:
                    async for msg in pubsub.listen():
                        if msg.get("type") == "pmessage":
                            try:
                                _offer(queue, json.loads(msg["data"]))
                            except ValueError:
                                continue

                pump = asyncio.create_task(_pump())
            yield {"type": "heartbeat", "time": utcnow().isoformat()}
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except TimeoutError:
                    yield {"type": "heartbeat", "time": utcnow().isoformat()}
                    continue
                if ev["id"] in seen:  # local + redis delivery of the same event
                    continue
                seen[ev["id"]] = None
                if len(seen) > 2048:
                    seen.pop(next(iter(seen)))
                yield {"type": "incident_event", **ev}
        finally:
            self._global.discard(queue)
            if pump:
                pump.cancel()
            if pubsub is not None:
                try:
                    await pubsub.punsubscribe()
                    await pubsub.aclose()
                except Exception:  # noqa: BLE001
                    log.debug("event_bus.global_unsubscribe_failed")

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
        self._redis_ok = None


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


async def emit(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return await get_event_bus().emit(*args, **kwargs)
