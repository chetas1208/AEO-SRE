"""Muse connector v1 tool implementations (tenant-scoped, OAuth)."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.experiments import window as vwindow
from app.integrations.muse.envelope import IntentEnvelope
from app.integrations.muse.tools import (
    CheckIntentOut,
    ListGapsIn,
    RecordFeedbackIn,
    ToolContext,
    check_intent,
    list_discovery_gaps,
    record_feedback,
)
from app.models.interventions import Experiment
from app.models.core import Incident
from app.oauth.service import MusePrincipal


class FindMatchesIn(BaseModel):
    intent: str = Field(min_length=2, max_length=500)
    constraints: dict[str, Any] = Field(default_factory=dict)


class MatchOut(BaseModel):
    product_id: str
    name: str
    fit_score: float
    matched_constraints: list[str]
    missing_constraints: list[str]
    evidence_quality: str


class FindMatchesOut(BaseModel):
    matches: list[MatchOut]
    source: str = "muse_v1"


def _constraint_statements(constraints: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i, (k, v) in enumerate(sorted(constraints.items())):
        out.append((f"c{i}", f"{k}: {v}"))
    return out


async def find_matches(session, principal: MusePrincipal, body: FindMatchesIn) -> FindMatchesOut:
    refs = _constraint_statements(body.constraints)
    req = [s for _, s in refs]
    env = IntentEnvelope(intent=body.intent, constraints=body.constraints, requirements=req[:10], ttl_seconds=600, source="muse")
    ctx = ToolContext(session=session, org_id=principal.organization_id, source_mode="LIVE",
                      request_id=None, now=vwindow.now())
    out: CheckIntentOut = await check_intent(ctx, env)
    satisfied = sum(1 for i in out.items if i.product_truth == "satisfied")
    total = max(len(out.items), 1)
    fit = round(satisfied / total, 2)
    matched = [i.ref for i in out.items if i.product_truth == "satisfied"]
    missing = [i.ref for i in out.items if i.product_truth != "satisfied"]
    quality = "high" if out.ai_perception == "available" and not any(i.degraded for i in out.items) else "medium"
    if out.canonical_claims_considered == 0:
        quality = "low"
    return FindMatchesOut(matches=[MatchOut(
        product_id=str(principal.organization_id),
        name=body.intent.replace("_", " ").title(),
        fit_score=fit,
        matched_constraints=matched,
        missing_constraints=missing,
        evidence_quality=quality,
    )])


async def check_ai_perception(session, principal: MusePrincipal, product_id: str) -> dict[str, Any]:
    ctx = ToolContext(session=session, org_id=principal.organization_id, source_mode="LIVE",
                      request_id=None, now=vwindow.now())
    gaps = await list_discovery_gaps(ctx, ListGapsIn(limit=20))
    items = []
    for g in gaps.items:
        items.append({
            "claim": g.canonical_statement or g.perceived_claim or "discovery gap",
            "verified_truth": g.canonical_statement,
            "ai_perception": g.perceived_claim,
            "confidence": g.confidence or "unknown",
        })
    return {"product_id": product_id, "discovery_gaps": items, "source": "muse_v1", "profound_configured": gaps.note is None}


async def campaign_summary(session, principal: MusePrincipal, campaign_id: str) -> dict[str, Any]:
    from app.api.routes import campaigns as camp_mod

    for c in camp_mod.CAMPAIGNS_DB:
        org = c.get("organization_id")
        if c["id"] == campaign_id and (org is None or str(org) == str(principal.organization_id)):
            return {
                "campaign_id": c["id"],
                "name": c["name"],
                "status": c["status"],
                "total_cost": c.get("total_cost"),
                "roi": c.get("roi"),
                "measurement_confidence": c.get("measurement_confidence"),
                "operational_metrics": c.get("operational_metrics"),
                "source": "muse_v1",
            }
    return {"error": "not_found"}


async def experiment_status(session, principal: MusePrincipal, experiment_id: str) -> dict[str, Any]:
    try:
        eid = uuid.UUID(experiment_id)
    except ValueError:
        return {"error": "not_found"}
    row = (await session.execute(
        select(Experiment, Incident).join(Incident, Incident.id == Experiment.incident_id).where(
            Experiment.id == eid, Incident.org_id == principal.organization_id))).first()
    if row is None:
        return {"error": "not_found"}
    exp, inc = row
    return {
        "experiment_id": str(exp.id),
        "code": f"EXP-{exp.number:04d}",
        "status": str(exp.status),
        "hypothesis": exp.reason,
        "intervention": str(exp.selected_action),
        "incident": inc.title,
        "verification_window": exp.verification_window_start.isoformat() if exp.verification_window_start else None,
        "source": "muse_v1",
    }


async def record_feedback_v1(session, principal: MusePrincipal, body: RecordFeedbackIn):
    ctx = ToolContext(session=session, org_id=principal.organization_id, source_mode="LIVE",
                      request_id=None, now=vwindow.now())
    return await record_feedback(ctx, body)
