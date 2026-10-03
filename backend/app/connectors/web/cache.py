"""URL -> FetchResult cache. Redis when reachable, in-memory TTL fallback otherwise."""

from __future__ import annotations

import hashlib
import json
import time

import structlog

from app.connectors.web.types import FetchResult
from app.core.config import get_settings

log = structlog.get_logger()
REDIS_RETRY_SECONDS = 30.0


class ResultCache:
    def __init__(
        self,
        redis_url: str | None = None,
        ttl_seconds: int = 900,
        namespace: str = "aeo:web:fetch:",
        use_redis: bool = True,
        max_memory_entries: int = 2000,
    ) -> None:
        self.ttl = ttl_seconds
        self.namespace = namespace
        self._redis_url = redis_url if redis_url is not None else get_settings().redis_url
        self._use_redis = use_redis
        self._redis = None
        self._redis_down_until = 0.0
        self._mem: dict[str, tuple[float, str]] = {}
        self._max = max_memory_entries
        self.backend = "memory"
        self.hits = 0
        self.misses = 0

    def key(self, url: str) -> str:
        return self.namespace + hashlib.sha256(url.encode()).hexdigest()

    async def _client(self):
        if not self._use_redis or time.monotonic() < self._redis_down_until:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                client = aioredis.from_url(
                    self._redis_url, decode_responses=True, socket_connect_timeout=1.5, socket_timeout=1.5
                )
                await client.ping()
                self._redis = client
            except Exception as exc:
                self._mark_down(exc)
                return None
        return self._redis

    def _mark_down(self, exc: Exception) -> None:
        if self._redis is not None:
            self._redis = None
        self._redis_down_until = time.monotonic() + REDIS_RETRY_SECONDS
        self.backend = "memory"
        log.warning("web_cache.redis_unavailable", error=str(exc))

    async def get(self, url: str) -> FetchResult | None:
        key = self.key(url)
        raw: str | None = None
        client = await self._client()
        if client is not None:
            try:
                raw = await client.get(key)
                self.backend = "redis"
            except Exception as exc:
                self._mark_down(exc)
        if raw is None:
            entry = self._mem.get(key)
            if entry and entry[0] > time.monotonic():
                raw = entry[1]
            elif entry:
                self._mem.pop(key, None)
        if raw is None:
            self.misses += 1
            return None
        try:
            result = FetchResult.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            self.misses += 1
            return None
        self.hits += 1
        result.from_cache = True
        return result

    async def set(self, url: str, result: FetchResult, ttl: int | None = None) -> None:
        key, ttl = self.key(url), (self.ttl if ttl is None else ttl)
        payload = json.dumps(result.to_dict())
        if len(self._mem) >= self._max:
            self._mem.pop(next(iter(self._mem)), None)
        self._mem[key] = (time.monotonic() + ttl, payload)
        client = await self._client()
        if client is not None:
            try:
                await client.set(key, payload, ex=ttl)
                self.backend = "redis"
            except Exception as exc:
                self._mark_down(exc)

    async def delete(self, url: str) -> None:
        key = self.key(url)
        self._mem.pop(key, None)
        client = await self._client()
        if client is not None:
            try:
                await client.delete(key)
            except Exception as exc:
                self._mark_down(exc)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
