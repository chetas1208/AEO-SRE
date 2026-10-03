"""Read-only Mixpanel HTTP client (Export + event metadata)."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import structlog

from app.core.config import Settings, get_settings
from app.integrations.mixpanel.auth import api_host, basic_auth_header, mixpanel_configured

log = structlog.get_logger()


class MixpanelError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class MixpanelClient:
    """Service-account authenticated read client. No writes to Mixpanel."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.configured = mixpanel_configured(self.settings)
        self.project_id = (self.settings.mixpanel_project_id or "").strip()
        self._host = api_host(self.settings)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": basic_auth_header(self.settings), "Accept": "application/json"}

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       timeout: float = 30.0, max_attempts: int = 4) -> httpx.Response:
        url = f"{self._host}{path}"
        delay = 0.5
        last_exc: Exception | None = None
        for _attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(method, url, headers=self._headers(), params=params)
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.isdigit() else delay
                    raise MixpanelError("rate limited", status=429, retry_after=wait)
                if resp.status_code in (401, 403):
                    raise MixpanelError("auth failed", status=resp.status_code)
                if resp.status_code >= 500:
                    raise MixpanelError(f"upstream {resp.status_code}", status=resp.status_code)
                return resp
            except MixpanelError as exc:
                last_exc = exc
                if exc.status == 429 or (exc.status and exc.status >= 500):
                    wait = (exc.retry_after or delay) + random.uniform(0, 0.25)
                    await asyncio.sleep(wait)
                    delay = min(delay * 2, 30.0)
                    continue
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                await asyncio.sleep(delay + random.uniform(0, 0.25))
                delay = min(delay * 2, 30.0)
        raise MixpanelError(str(last_exc or "request failed"))

    async def ping_auth(self) -> tuple[bool, float, str | None]:
        """Minimal read: event names for today (safe metadata). Returns (ok, latency_ms, error)."""
        if not self.configured:
            return False, 0.0, "not configured"
        t0 = time.perf_counter()
        try:
            today = date.today().isoformat()
            resp = await self._request(
                "GET",
                "/api/2.0/events/names",
                params={"project_id": self.project_id, "type": "general", "from_date": today, "to_date": today},
                timeout=20.0,
            )
            if resp.status_code != 200:
                return False, round((time.perf_counter() - t0) * 1000, 1), f"HTTP {resp.status_code}"
            return True, round((time.perf_counter() - t0) * 1000, 1), None
        except MixpanelError as exc:
            return False, round((time.perf_counter() - t0) * 1000, 1), str(exc)

    async def event_names(self, from_date: date, to_date: date) -> list[str]:
        resp = await self._request(
            "GET",
            "/api/2.0/events/names",
            params={
                "project_id": self.project_id,
                "type": "general",
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            },
        )
        data = resp.json()
        if isinstance(data, list):
            return [str(x) for x in data]
        return []

    async def export_events(self, from_date: date, to_date: date, *, limit_lines: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Stream export API JSONL (read-only)."""
        export_host = "https://data-eu.mixpanel.com" if "eu." in self._host else "https://data.mixpanel.com"
        url = f"{export_host}/api/2.0/export/"
        async with httpx.AsyncClient(timeout=120.0) as http:
            resp = await http.get(
                url,
                headers=self._headers(),
                params={
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                    "project_id": self.project_id,
                },
            )
        if resp.status_code == 429:
            raise MixpanelError("rate limited", status=429)
        if resp.status_code in (401, 403):
            raise MixpanelError("auth failed", status=resp.status_code)
        if resp.status_code != 200:
            raise MixpanelError(f"export HTTP {resp.status_code}", status=resp.status_code)
        count = 0
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            if limit_lines is not None and count >= limit_lines:
                break

    async def sample_events(self, *, days_back: int = 1, limit: int = 25) -> list[dict[str, Any]]:
        end = date.today()
        start = end
        out: list[dict[str, Any]] = []
        async for row in self.export_events(start, end, limit_lines=limit):
            out.append(row)
        if out:
            return out
        # widen window once for sparse projects
        from datetime import timedelta

        start = end - timedelta(days=days_back)
        async for row in self.export_events(start, end, limit_lines=limit):
            out.append(row)
        return out
