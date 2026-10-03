"""Async Profound REST client (read-only).

Base URL https://api.tryprofound.com, auth `X-API-Key`. Docs: 600 req/hour/key, 429 carries Retry-After and
X-RateLimit-{Limit,Remaining,Reset}. 5xx/transport errors are retried with exponential backoff + jitter.
Raw bodies are handed to an optional `RawPayloadStore` unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Self

import httpx
import structlog

from app.core.config import get_settings
from app.domain.enums import CapabilityState

from .errors import (
    ProfoundAuthError,
    ProfoundError,
    ProfoundNotConfigured,
    ProfoundNotFound,
    ProfoundPermissionError,
    ProfoundRateLimited,
    ProfoundResponseError,
    ProfoundServerError,
    ProfoundTransportError,
    ProfoundValidationError,
)
from .requests import (
    AnswersQuery,
    CitationsQuery,
    FactCheckClaimsQuery,
    FactCheckQuery,
    PromptsListQuery,
    QueryFanoutsQuery,
    VisibilityQuery,
    VolumeQuery,
)
from .store import RawPayloadStore

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.tryprofound.com"
RETRYABLE_STATUS = {500, 502, 503, 504}
_CAP_CACHE: dict[tuple, tuple[float, Any]] = {}


@dataclass
class ProfoundResponse:
    status: int
    data: Any
    endpoint: str
    raw_ref: str | None = None
    rate_limit: dict[str, int | None] = field(default_factory=dict)

    @property
    def next_cursor(self) -> str | None:
        info = self.data.get("info") if isinstance(self.data, dict) else None
        if isinstance(info, dict):
            return info.get("next_cursor") or None
        pag = self.data.get("pagination") if isinstance(self.data, dict) else None
        if isinstance(pag, dict):
            return pag.get("next_cursor") or None
        return None


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def parse_retry_after(headers: httpx.Headers, now: Callable[[], float] = time.time) -> float | None:
    """Retry-After as seconds or HTTP-date; falls back to X-RateLimit-Reset (unix ts)."""
    ra = headers.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(ra).timestamp() - now())
            except (TypeError, ValueError):
                pass
    reset = _int(headers.get("x-ratelimit-reset"))
    if reset:
        return max(0.0, reset - now())
    return None


class ProfoundClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 20.0,
        max_retry_after: float = 60.0,
        raw_store: RawPayloadStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        capability_ttl: float = 300.0,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.profound_api_key
        self.base_url = (base_url if base_url is not None else settings.profound_base_url) or DEFAULT_BASE_URL
        self.base_url = self.base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.max_retry_after = max_retry_after
        self.raw_store = raw_store
        self._sleep = sleep
        self._capability_ttl = capability_ttl
        self.last_rate_limit: dict[str, int | None] = {}
        self._http = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=transport,
            headers={"Accept": "application/json", "User-Agent": "aeo-sre/0.1"},
        )

    # -- lifecycle --------------------------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def __repr__(self) -> str:  # never leak the key
        return f"ProfoundClient(base_url={self.base_url!r}, configured={self.configured})"

    # -- core request -----------------------------------------------------------------------------------
    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_max, self.backoff_base * (2**attempt)) * (0.5 + random.random() / 2)

    async def request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None, persist: bool = True
    ) -> ProfoundResponse:
        """One structured log line per logical request (provider, endpoint, status, duration). Never the key/body."""
        started = time.perf_counter()
        try:
            resp = await self._request(method, path, json=json, params=params, persist=persist)
        except ProfoundError as exc:
            log.info("provider.request", provider="profound", method=method, endpoint=path, status=exc.status or 0,
                     ok=False, error=type(exc).__name__, duration_ms=round((time.perf_counter() - started) * 1000, 1))
            raise
        log.info("provider.request", provider="profound", method=method, endpoint=path, status=resp.status, ok=True,
                 duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return resp

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None, persist: bool = True
    ) -> ProfoundResponse:
        if not self.configured:
            raise ProfoundNotConfigured("PROFOUND_API_KEY is not set", endpoint=path)
        headers = {"X-API-Key": self._api_key}
        last: ProfoundError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._http.request(method, path, json=json, params=params, headers=headers)
            except httpx.TimeoutException as e:
                last = ProfoundTransportError(f"timeout: {type(e).__name__}", endpoint=path)
            except httpx.TransportError as e:
                last = ProfoundTransportError(f"transport error: {type(e).__name__}", endpoint=path)
            else:
                self.last_rate_limit = {
                    "limit": _int(resp.headers.get("x-ratelimit-limit")),
                    "remaining": _int(resp.headers.get("x-ratelimit-remaining")),
                    "reset": _int(resp.headers.get("x-ratelimit-reset")),
                }
                if resp.status_code < 300:
                    return await self._ok(resp, method, path, json, params, persist)
                err = self._map_error(resp, path)
                if isinstance(err, ProfoundRateLimited):
                    wait = err.retry_after
                    if wait is not None and wait > self.max_retry_after:
                        raise err  # too long to sit on; caller degrades
                    last = err
                    if attempt < self.max_retries:
                        await self._sleep(wait if wait is not None else self._backoff(attempt))
                        continue
                    raise err
                if resp.status_code in RETRYABLE_STATUS:
                    last = err
                else:
                    raise err
            if attempt < self.max_retries:
                await self._sleep(self._backoff(attempt))
        assert last is not None
        raise last

    async def _ok(self, resp: httpx.Response, method, path, body, params, persist) -> ProfoundResponse:
        try:
            data = resp.json()
        except ValueError as e:
            raise ProfoundResponseError("response body is not JSON", status=resp.status_code, endpoint=path) from e
        ref = None
        if persist and self.raw_store is not None:
            try:
                ref = await self.raw_store.save(
                    endpoint=f"{method} {path}", request={"json": body, "params": params}, payload=data,
                    fetched_at=datetime.now(UTC),
                )
            except Exception as e:  # noqa: BLE001 - persistence must never break a read
                log.warning("profound.raw_store_failed", error=type(e).__name__)
        return ProfoundResponse(resp.status_code, data, path, ref, dict(self.last_rate_limit))

    def _map_error(self, resp: httpx.Response, path: str) -> ProfoundError:
        try:
            detail: Any = resp.json()
        except ValueError:
            detail = resp.text[:300]
        s = resp.status_code
        kw = {"status": s, "endpoint": path, "detail": detail}
        if s == 401:
            return ProfoundAuthError("invalid or missing API key", **kw)
        if s == 403:
            return ProfoundPermissionError("insufficient permissions", **kw)
        if s == 404:
            return ProfoundNotFound("not found", **kw)
        if s in (400, 422):
            return ProfoundValidationError("request rejected by Profound", **kw)
        if s == 429:
            return ProfoundRateLimited("rate limited", retry_after=parse_retry_after(resp.headers), **kw)
        if s >= 500:
            return ProfoundServerError("Profound server error", **kw)
        return ProfoundError(f"unexpected status {s}", **kw)

    # -- pagination -------------------------------------------------------------------------------------
    async def paginate_post(
        self, path: str, base: dict[str, Any], *, max_pages: int = 20
    ) -> AsyncIterator[ProfoundResponse]:
        cursor: str | None = base.get("cursor")
        for _ in range(max_pages):
            body = dict(base)
            if cursor:
                body["cursor"] = cursor
            resp = await self.request("POST", path, json=body)
            yield resp
            cursor = resp.next_cursor
            if not cursor:
                return

    # -- org / discovery (v1, GET) ----------------------------------------------------------------------
    async def list_organizations(self) -> ProfoundResponse:
        return await self.request("GET", "/v1/org")

    async def list_categories(self) -> ProfoundResponse:
        return await self.request("GET", "/v1/org/categories")

    async def list_category_assets(self, category_id: str) -> ProfoundResponse:
        return await self.request("GET", f"/v1/org/categories/{category_id}/assets")

    async def list_topics(self, category_id: str) -> ProfoundResponse:
        return await self.request("GET", f"/v1/org/categories/{category_id}/topics")

    async def list_personas(self, category_id: str) -> ProfoundResponse:
        return await self.request("GET", f"/v1/org/categories/{category_id}/personas")

    async def list_regions(self) -> ProfoundResponse:
        return await self.request("GET", "/v1/org/regions")

    async def list_models(self) -> ProfoundResponse:
        return await self.request("GET", "/v1/org/models")

    async def list_domains(self) -> ProfoundResponse:
        return await self.request("GET", "/v1/org/domains")

    async def list_prompts(self, category_id: str, query: PromptsListQuery | None = None) -> ProfoundResponse:
        q = query or PromptsListQuery()
        return await self.request("GET", f"/v1/org/categories/{category_id}/prompts", params=q.params())

    async def list_agents(self, *, limit: int = 100) -> ProfoundResponse:
        return await self.request("GET", "/v1/agents", params={"limit": limit})

    # -- reports (v2, POST but read-only) ---------------------------------------------------------------
    async def visibility(self, q: VisibilityQuery) -> ProfoundResponse:
        return await self.request("POST", "/v2/reports/visibility", json=q.payload())

    async def citations(self, q: CitationsQuery) -> ProfoundResponse:
        return await self.request("POST", "/v2/reports/citations", json=q.payload())

    async def factcheck(self, q: FactCheckQuery) -> ProfoundResponse:
        return await self.request("POST", "/v2/reports/factcheck", json=q.payload())

    async def factcheck_claims(self, q: FactCheckClaimsQuery) -> ProfoundResponse:
        return await self.request("POST", "/v2/reports/factcheck/claims", json=q.payload())

    async def query_fanouts(self, q: QueryFanoutsQuery) -> ProfoundResponse:
        return await self.request("POST", "/v2/reports/query-fanouts", json=q.payload())

    async def answers(self, q: AnswersQuery) -> ProfoundResponse:
        """Detailed answer rows. Call this for an open incident, not on every scheduled ingest."""
        return await self.request("POST", "/v2/prompts/answers", json=q.payload())

    async def prompt_volume(self, q: VolumeQuery) -> ProfoundResponse:
        return await self.request("POST", "/v2/prompt-volumes/volume/on-the-fly", json=q.payload())

    def iter_visibility(self, q: VisibilityQuery, *, max_pages: int = 20) -> AsyncIterator[ProfoundResponse]:
        return self.paginate_post("/v2/reports/visibility", q.payload(), max_pages=max_pages)

    def iter_citations(self, q: CitationsQuery, *, max_pages: int = 20) -> AsyncIterator[ProfoundResponse]:
        return self.paginate_post("/v2/reports/citations", q.payload(), max_pages=max_pages)

    def iter_factcheck(self, q: FactCheckQuery, *, max_pages: int = 20) -> AsyncIterator[ProfoundResponse]:
        return self.paginate_post("/v2/reports/factcheck", q.payload(), max_pages=max_pages)

    def iter_query_fanouts(self, q: QueryFanoutsQuery, *, max_pages: int = 20) -> AsyncIterator[ProfoundResponse]:
        return self.paginate_post("/v2/reports/query-fanouts", q.payload(), max_pages=max_pages)

    # -- capabilities -----------------------------------------------------------------------------------
    async def capability_report(self, *, category_id: str | None = None, force: bool = False):
        """Detailed per-surface report (`CapabilityStatus`). Never raises."""
        from .capabilities import probe_capabilities

        # shared across instances: callers (system status polling) create short-lived clients
        key = (self.base_url, hashlib.sha256(self._api_key.encode()).hexdigest()[:12], category_id)
        hit = _CAP_CACHE.get(key)
        if not force and hit and time.monotonic() - hit[0] < self._capability_ttl:
            return hit[1]
        report = await probe_capabilities(self, category_id=category_id)
        if self.configured:
            _CAP_CACHE[key] = (time.monotonic(), report)
        return report

    async def capabilities(self, *, category_id: str | None = None, force: bool = False) -> dict[str, CapabilityState]:
        """surface -> CapabilityState. Never raises: any failure degrades/unavailable."""
        try:
            report = await self.capability_report(category_id=category_id, force=force)
            return {k: v.state for k, v in report.items()}
        except Exception as e:  # noqa: BLE001 - capability reporting must not break callers
            log.warning("profound.capabilities_failed", error=type(e).__name__)
            from .capabilities import SURFACES

            return {s: CapabilityState.UNAVAILABLE for s in SURFACES}

