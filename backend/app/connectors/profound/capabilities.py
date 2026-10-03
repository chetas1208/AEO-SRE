"""Capability probes: one lightweight read per surface. Never raises; never fabricates a healthy state.

State: HEALTHY only when a live probe returned the documented shape. `verification`:
  live_verified - a probe against the real API succeeded just now
  unverified    - no key / probe failed / not probed; behaviour is docs-only
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from app.domain.enums import CapabilityState

from .errors import (
    ProfoundAuthError,
    ProfoundError,
    ProfoundNotConfigured,
    ProfoundNotFound,
    ProfoundPermissionError,
    ProfoundRateLimited,
    ProfoundValidationError,
)
from .requests import (
    CitationsQuery,
    FactCheckQuery,
    PromptsListQuery,
    QueryFanoutsQuery,
    VisibilityQuery,
    VolumeQuery,
)
from .timeutil import last_complete_day

if TYPE_CHECKING:
    from .client import ProfoundClient

log = structlog.get_logger(__name__)

SURFACES = ("visibility", "citations", "prompts", "prompt_volume", "competitors", "factcheck", "query_fanouts", "agents")

# How well the public docs/OpenAPI pin the surface down (independent of live results).
DOC_CONFIDENCE = {
    "visibility": "documented (v2 beta)",
    "citations": "documented (v2 beta)",
    "prompts": "documented (v1)",
    "prompt_volume": "documented (keyword-level, beta); mapping from prompts/topics is our assumption",
    "competitors": "documented (assets + visibility scope=all)",
    "factcheck": "documented (v2 beta); requires FactCheck setup on the category",
    "query_fanouts": "documented (v2); share and query text only, shifts are computed here",
    "agents": "documented (v1; list only, runs not invoked by this connector)",
}


@dataclass(frozen=True)
class CapabilityStatus:
    state: CapabilityState
    verification: str  # live_verified | unverified
    reason: str | None
    endpoint: str
    doc_confidence: str
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value, "verification": self.verification, "reason": self.reason,
            "endpoint": self.endpoint, "doc_confidence": self.doc_confidence,
            "checked_at": self.checked_at.isoformat(),
        }


ENDPOINTS = {
    "visibility": "POST /v2/reports/visibility",
    "citations": "POST /v2/reports/citations",
    "prompts": "GET /v1/org/categories/{id}/prompts",
    "prompt_volume": "POST /v2/prompt-volumes/volume/on-the-fly",
    "competitors": "GET /v1/org/categories/{id}/assets",
    "factcheck": "POST /v2/reports/factcheck",
    "query_fanouts": "POST /v2/reports/query-fanouts",
    "agents": "GET /v1/agents",
}


def status_from_error(e: BaseException) -> tuple[CapabilityState, str]:
    if isinstance(e, ProfoundNotConfigured):
        return CapabilityState.UNAVAILABLE, "not_configured"
    if isinstance(e, ProfoundAuthError):
        return CapabilityState.UNAVAILABLE, "auth_failed"
    if isinstance(e, ProfoundPermissionError):
        return CapabilityState.UNAVAILABLE, "forbidden_or_not_enabled"
    if isinstance(e, ProfoundNotFound):
        return CapabilityState.UNAVAILABLE, "not_found"
    if isinstance(e, ProfoundValidationError):
        return CapabilityState.DEGRADED, "request_rejected"
    if isinstance(e, ProfoundRateLimited):
        return CapabilityState.DEGRADED, "rate_limited"
    if isinstance(e, ProfoundError):
        return CapabilityState.DEGRADED, f"error:{type(e).__name__}"
    return CapabilityState.DEGRADED, f"error:{type(e).__name__}"


def _all(state: CapabilityState, reason: str) -> dict[str, CapabilityStatus]:
    now = datetime.now(UTC)
    return {s: CapabilityStatus(state, "unverified", reason, ENDPOINTS[s], DOC_CONFIDENCE[s], now) for s in SURFACES}


def _is_envelope(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("data"), list)


async def _probe(name: str, fn) -> CapabilityStatus:
    now = datetime.now(UTC)
    try:
        ok, reason = await fn()
    except Exception as e:  # noqa: BLE001 - probes must never raise
        state, reason = status_from_error(e)
        return CapabilityStatus(state, "unverified", reason, ENDPOINTS[name], DOC_CONFIDENCE[name], now)
    if ok:
        return CapabilityStatus(CapabilityState.HEALTHY, "live_verified", reason, ENDPOINTS[name], DOC_CONFIDENCE[name], now)
    return CapabilityStatus(CapabilityState.DEGRADED, "unverified", reason, ENDPOINTS[name], DOC_CONFIDENCE[name], now)


async def _pick_probe_category(client: ProfoundClient, cats: list[Any]) -> dict[str, Any]:
    """First category that has an OWNED asset (an empty/placeholder category would make every probe vacuous);
    falls back to the first category."""
    cats = [c for c in cats if isinstance(c, dict) and c.get("id")]
    for c in cats[:10]:
        try:
            r = await client.list_category_assets(str(c["id"]))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(r.data, list) and any(isinstance(a, dict) and a.get("is_owned") for a in r.data):
            return c
    return cats[0] if cats else {}


async def probe_capabilities(client: ProfoundClient, *, category_id: str | None = None) -> dict[str, CapabilityStatus]:
    if not client.configured:
        return _all(CapabilityState.UNAVAILABLE, "not_configured")
    category_name = None
    if category_id is None:
        try:
            resp = await client.list_categories()
        except Exception as e:  # noqa: BLE001
            state, reason = status_from_error(e)
            return _all(state, f"categories:{reason}")
        cats = resp.data if isinstance(resp.data, list) else []
        if not cats:
            return _all(CapabilityState.UNAVAILABLE, "no_category_accessible")
        chosen = await _pick_probe_category(client, cats)
        category_id, category_name = str(chosen.get("id")), chosen.get("name")
    end = last_complete_day()
    start = end - timedelta(days=1)
    s, e = start.isoformat(), end.isoformat()

    async def visibility():
        r = await client.visibility(VisibilityQuery(category_id=category_id, start_date=s, end_date=e,
                                                    metrics=["visibility_score"], limit=1))
        return (_is_envelope(r.data), None if _is_envelope(r.data) else "unexpected_shape")

    async def citations():
        r = await client.citations(CitationsQuery(category_id=category_id, start_date=s, end_date=e,
                                                  metrics=["citation_share"], limit=1))
        return (_is_envelope(r.data), None if _is_envelope(r.data) else "unexpected_shape")

    async def prompts():
        r = await client.list_prompts(category_id, PromptsListQuery(limit=1))
        ok = _is_envelope(r.data)
        return ok, None if ok else "unexpected_shape"

    async def prompt_volume():
        kw = category_name or "ai search"
        r = await client.prompt_volume(VolumeQuery(keyword=kw, start_date=start - timedelta(days=30), end_date=end))
        return (_is_envelope(r.data), None if _is_envelope(r.data) else "unexpected_shape")

    async def competitors():
        r = await client.list_category_assets(category_id)
        if not isinstance(r.data, list):
            return False, "unexpected_shape"
        if not any(a.get("is_owned") is False for a in r.data if isinstance(a, dict)):
            return False, "no_competitor_assets_tracked"
        return True, None

    async def factcheck():
        r = await client.factcheck(FactCheckQuery(category_id=category_id, start_date=s, end_date=e, limit=1))
        return (_is_envelope(r.data), None if _is_envelope(r.data) else "unexpected_shape")

    async def query_fanouts():
        r = await client.query_fanouts(QueryFanoutsQuery(
            category_id=category_id, start_date=s, end_date=e, metrics=["total_fanouts"], limit=1,
        ))
        return (_is_envelope(r.data), None if _is_envelope(r.data) else "unexpected_shape")

    async def agents():
        r = await client.list_agents(limit=1)
        return (_is_envelope(r.data), None if _is_envelope(r.data) else "unexpected_shape")

    fns = {"visibility": visibility, "citations": citations, "prompts": prompts, "prompt_volume": prompt_volume,
           "competitors": competitors, "factcheck": factcheck, "query_fanouts": query_fanouts, "agents": agents}
    results = await asyncio.gather(*(_probe(n, f) for n, f in fns.items()))
    return dict(zip(fns, results, strict=True))
