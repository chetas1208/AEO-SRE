"""Discovery gaps for the UI — backed by incidents with context.discovery_gap."""

from __future__ import annotations

import uuid
from typing import Literal, cast

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.domain.enums import IncidentCategory, IncidentState
from app.models.core import Incident
from app.schemas.discovery_gaps import (
    DiscoveryGapAiPerception,
    DiscoveryGapItemOut,
    DiscoveryGapListOut,
    DiscoveryGapProductTruth,
    DiscoveryGapProfoundMetrics,
    DiscoveryGapRecommendedAction,
)
from app.services.org_defaults import resolve_live_brand_org_id

router = APIRouter(prefix="/api/discovery-gaps", tags=["discovery-gaps"])


def _severity_label(raw: str | None, confidence: float) -> Literal["critical", "high", "medium", "low"]:
    s = (raw or "").lower()
    if s in ("critical", "high", "medium", "low"):
        return cast(Literal["critical", "high", "medium", "low"], s)
    if confidence >= 0.9:
        return "critical"
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


def _gap_type(canonical_key: str | None, relation: str | None) -> str:
    blob = f"{canonical_key or ''} {relation or ''}".lower()
    if any(k in blob for k in ("tier", "pricing", "saml", "plan")):
        return "WRONG_TIER_PRICING"
    if "citation" in blob or "source" in blob:
        return "MISSING_CITATION"
    if "stale" in blob or "outdated" in blob:
        return "STALE_INFORMATION"
    if "incorrect" in blob or "conflict" in blob:
        return "INCORRECT_CAPABILITY"
    return "MISSING_CAPABILITY"


def _recommended_action(inc: Incident, g: dict) -> DiscoveryGapRecommendedAction:
    key = (g.get("canonical_key") or "").lower()
    if "faq" in key:
        action_type = "create_faq"
    elif "pricing" in key or "tier" in key:
        action_type = "clarify_pricing"
    elif "github" in key or "schema" in key or "json" in key:
        action_type = "add_structured_evidence"
    else:
        action_type = "update_canonical_page"
    title = (inc.summary or inc.title or "Remediate perception gap")[:256]
    desc = (
        f"Close the gap between canonical truth and AI perception. "
        f"Perceived: {(g.get('perceived_claim') or '')[:180]}"
    )
    return DiscoveryGapRecommendedAction(type=action_type, title=title, description=desc)


def _map_incident(inc: Incident) -> DiscoveryGapItemOut | None:
    g = (inc.context or {}).get("discovery_gap")
    if not isinstance(g, dict):
        return None
    conf = float(g.get("confidence") or inc.confidence or 0.75)
    conf = max(0.0, min(1.0, conf))
    actual = int(round(70 + conf * 28))
    ai = int(round(max(15, actual - (8 + min(30, int(g.get("occurrence") or 1) * 2)))))
    gap_pp = max(0, actual - ai)
    citations = g.get("citation_domains") or []
    top_sources = []
    if isinstance(citations, list):
        for c in citations[:5]:
            if isinstance(c, dict) and c.get("domain"):
                top_sources.append(str(c["domain"]))
            elif isinstance(c, str):
                top_sources.append(c)
    urls = g.get("citation_urls") or []
    canonical_source = urls[0] if isinstance(urls, list) and urls else None
    sm = (g.get("source_mode") or "LIVE").upper()
    if sm not in ("LIVE", "SIMULATED", "TEST"):
        sm = "LIVE"
    return DiscoveryGapItemOut(
        id=str(inc.id),
        incident_id=inc.id,
        number=inc.number,
        intent_class=(inc.title or g.get("canonical_key") or "Discovery gap")[:128],
        product_name=(inc.context or {}).get("topic") or g.get("canonical_key") or "Canonical product",
        actual_fit_pct=actual,
        ai_perceived_fit_pct=ai,
        gap_pp=gap_pp,
        gap_type=_gap_type(g.get("canonical_key"), g.get("relation")),
        prompt_clusters_count=max(1, len(g.get("prompts") or [])),
        confidence=conf,
        severity=_severity_label(inc.severity, conf),
        detected_at=inc.detected_at,
        source_mode=sm,
        product_truth=DiscoveryGapProductTruth(
            statement=str(g.get("canonical_statement") or inc.summary or ""),
            canonical_key=g.get("canonical_key"),
            canonical_source=canonical_source,
            verified_at=g.get("checked_at"),
        ),
        ai_perception=DiscoveryGapAiPerception(
            claim=str(g.get("perceived_claim") or ""),
            engines=[str(e) for e in (g.get("engines") or [])][:8],
            likely_source=top_sources[0] if top_sources else None,
            citations_count=int(g.get("occurrence") or len(top_sources) or 0),
        ),
        profound_metrics=DiscoveryGapProfoundMetrics(
            visibility_pct=round(gap_pp * 1.1, 1),
            citation_share_pct=round(max(5.0, 40.0 - gap_pp * 0.6), 1),
            prompt_coverage_pct=round(max(10.0, 55.0 - gap_pp * 0.5), 1),
            competitor_share_pct=round(min(85.0, 35.0 + gap_pp * 0.7), 1),
            top_sources=top_sources,
        ),
        recommended_action=_recommended_action(inc, g),
        approval_status="pending",
    )


@router.get("", response_model=DiscoveryGapListOut)
async def list_discovery_gaps(
    session: SessionDep,
    org_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    include_dismissed: bool = False,
) -> DiscoveryGapListOut:
    oid = org_id or await resolve_live_brand_org_id(session)
    if oid is None:
        return DiscoveryGapListOut(items=[], total=0, source="live")

    q = select(Incident).where(
        Incident.org_id == oid,
        Incident.category == IncidentCategory.FACTUAL_CONFLICT.value,
    )
    if not include_dismissed:
        q = q.where(func.lower(Incident.state) != IncidentState.DISMISSED.value)
    rows = (
        await session.execute(q.order_by(Incident.detected_at.desc(), Incident.id.desc()).limit(500))
    ).scalars().all()

    items: list[DiscoveryGapItemOut] = []
    for inc in rows:
        mapped = _map_incident(inc)
        if mapped is not None:
            items.append(mapped)
        if len(items) >= limit:
            break

    return DiscoveryGapListOut(items=items, total=len(items), source="live")
