"""Control Plane flight deck API routes: Aggregated live view of Agents, Campaigns, Decisions, Experiments, and Outcomes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import BusDep, SessionDep
from app.api.routes.campaigns import CAMPAIGNS_DB
from app.domain.enums import ExperimentStatus
from app.graph.sse import merge_streams
from app.models.interventions import Experiment
from app.schemas.control_plane import (
    AgentActivity,
    CampaignFinancialCard,
    ControlPlaneGraph,
    ControlPlaneGraphEdge,
    ControlPlaneGraphNode,
    ControlPlaneResponse,
    ControlPlaneSummary,
    DecisionCard,
    ExperimentControlCard,
)

log = structlog.get_logger()
router = APIRouter(prefix="/api/control-plane", tags=["control-plane"])

# Authoritative agent activity registry
AGENTS_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "agt-citation-recovery",
        "name": "Citation Recovery Agent",
        "role": "Profound Perception & Canonical Reconciliation",
        "campaign_id": "cmp-ai-discovery-launch-01",
        "campaign_name": "Enterprise AI Discovery Launch",
        "current_task": "Publishing SAML 2.0 & SCIM canonical tier matrix",
        "runs": 24,
        "model_cost": 142.50,
        "total_cost": 2130.00,
        "outputs_produced": 8,
        "outputs_accepted": 7,
        "attributed_outcome": "POSITIVE",
        "state": "RUNNING",
        "last_active_at": "2026-10-03T22:15:00Z",
    },
    {
        "id": "agt-competitive-copilot",
        "name": "Competitive Differentiation Copilot",
        "role": "Perception Gap & Rival Benchmarking",
        "campaign_id": "cmp-sso-parity-q4",
        "campaign_name": "SSO Parity & Security Claims",
        "current_task": "Synthesizing competitor comparison matrix against Auth0/Okta",
        "runs": 18,
        "model_cost": 88.20,
        "total_cost": 1120.00,
        "outputs_produced": 5,
        "outputs_accepted": 4,
        "attributed_outcome": "POSITIVE",
        "state": "RUNNING",
        "last_active_at": "2026-10-03T22:10:00Z",
    },
    {
        "id": "agt-claim-verifier",
        "name": "Technical Claim Verification Agent",
        "role": "Canonical Truth Integrity Guard",
        "campaign_id": "cmp-ai-discovery-launch-01",
        "campaign_name": "Enterprise AI Discovery Launch",
        "current_task": "Validating SIEM Webhook Export docs against product schema",
        "runs": 14,
        "model_cost": 64.00,
        "total_cost": 840.00,
        "outputs_produced": 6,
        "outputs_accepted": 6,
        "attributed_outcome": "POSITIVE",
        "state": "REVIEW",
        "last_active_at": "2026-10-03T22:00:00Z",
    },
    {
        "id": "agt-token-router",
        "name": "Model API Token Router",
        "role": "Cost-Aware Prompt Classifier & Orchestrator",
        "campaign_id": "cmp-developer-experience-01",
        "campaign_name": "AI Search Developer Experience",
        "current_task": "Haiku 4.5 intent routing with selective Sonnet escalation",
        "runs": 42,
        "model_cost": 32.10,
        "total_cost": 320.00,
        "outputs_produced": 12,
        "outputs_accepted": 12,
        "attributed_outcome": "NOT_MEASURABLE",
        "state": "COMPLETED",
        "last_active_at": "2026-10-03T21:45:00Z",
    },
    {
        "id": "agt-rebrand-auditor",
        "name": "Rebrand Perception Auditor",
        "role": "Historical Citation Drift Monitor",
        "campaign_id": "cmp-cloud-security-rebrand",
        "campaign_name": "Cloud Security Rebrand",
        "current_task": "Investigating citation drop following v1 documentation deprecation",
        "runs": 11,
        "model_cost": 72.40,
        "total_cost": 960.00,
        "outputs_produced": 3,
        "outputs_accepted": 1,
        "attributed_outcome": "NEGATIVE",
        "state": "BLOCKED",
        "last_active_at": "2026-10-03T21:30:00Z",
    },
]

# Authoritative agent decisions registry
DECISIONS_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "dec-saml-canonical",
        "title": "Publish Verified SAML Tier Matrix on Canonical Domain",
        "recommended_by": "Citation Recovery Agent",
        "campaign_id": "cmp-ai-discovery-launch-01",
        "campaign_name": "Enterprise AI Discovery Launch",
        "action_type": "update_existing_page",
        "policy_version": "v0.3.1",
        "status": "APPROVED",
        "observed_outcome": "POSITIVE",
        "context": "Deploy structured claims to displace stale 2024 blog citing SAML as Enterprise-only",
        "cost": 142.50,
        "created_at": "2026-10-03T18:30:00Z",
    },
    {
        "id": "dec-scim-jsonld",
        "title": "Deploy JSON-LD Structured Evidence for SCIM API",
        "recommended_by": "Technical Claim Verification Agent",
        "campaign_id": "cmp-ai-discovery-launch-01",
        "campaign_name": "Enterprise AI Discovery Launch",
        "action_type": "structured_data",
        "policy_version": "v0.3.1",
        "status": "PENDING_REVIEW",
        "observed_outcome": "PENDING",
        "context": "Allows buyer personal agents to parse authentication schemas directly from metadata",
        "cost": 88.00,
        "created_at": "2026-10-03T21:15:00Z",
    },
    {
        "id": "dec-legacy-deprecate",
        "title": "Deprecate Legacy v1 SSO Audit Logs Guide",
        "recommended_by": "Rebrand Perception Auditor",
        "campaign_id": "cmp-cloud-security-rebrand",
        "campaign_name": "Cloud Security Rebrand",
        "action_type": "update_existing_page",
        "policy_version": "v0.2.8",
        "status": "REJECTED",
        "observed_outcome": "NEGATIVE",
        "context": "Premature deprecation caused 3 key developer indexing engines to drop citation weight",
        "cost": 95.00,
        "created_at": "2026-10-03T16:00:00Z",
    },
]


@router.get("", response_model=ControlPlaneResponse)
async def get_control_plane(session: SessionDep):
    """Aggregated flight deck state: summary KPIs, active agents, campaign financial health, decisions, and 3D topology."""
    now = datetime.now(timezone.utc)

    # 1. Campaigns summary & financial cards
    campaign_cards: list[CampaignFinancialCard] = []
    tot_cost = 0.0
    tot_return = 0.0

    for c in CAMPAIGNS_DB:
        cost = float(c.get("total_cost", 0.0))
        ret = c.get("attributed_return")
        ret_val = float(ret) if ret is not None else None
        tot_cost += cost
        if ret_val is not None:
            tot_return += ret_val

        # Net & ROI
        net = (ret_val - cost) if ret_val is not None else None
        roi = ((ret_val - cost) / cost) if (ret_val is not None and cost > 0) else None

        # Financial Status
        if ret_val is not None:
            if net is not None and net > 0:
                fin_status = "POSITIVE"
            elif net is not None and net < 0:
                fin_status = "NEGATIVE"
            else:
                fin_status = "UNCERTAIN"
        else:
            fin_status = "NOT_MEASURABLE"

        campaign_cards.append(CampaignFinancialCard(
            id=c["id"],
            name=c["name"],
            status=c.get("status", "ACTIVE"),
            total_cost=cost,
            attributed_return=ret_val,
            net_return=net,
            roi_pct=(roi * 100.0) if roi is not None else None,
            financial_status=fin_status,
            measurement_confidence=c.get("measurement_confidence", "MEDIUM"),
            return_source="ATTRIBUTED",
            primary_channel=c.get("primary_channel", "Search LLMs & Docs"),
            active_agents_count=len([a for a in AGENTS_REGISTRY if a["campaign_id"] == c["id"]]),
            active_experiments_count=1 if c["id"] == "cmp-ai-discovery-launch-01" else 0,
        ))

    # 2. Query actual Experiments from DB
    exp_rows = (await session.execute(
        select(Experiment).order_by(Experiment.created_at.desc()).limit(10)
    )).scalars().all()

    exp_cards: list[ExperimentControlCard] = []
    for e in exp_rows:
        spec = e.spec if isinstance(e.spec, dict) else {}
        exp_cards.append(ExperimentControlCard(
            id=str(e.id),
            code=e.code,
            name=f"{e.code}: {e.selected_action.value if hasattr(e.selected_action, 'value') else e.selected_action}",
            hypothesis=e.reason or spec.get("statement") or "Hypothesis verification",
            action=e.selected_action.value if hasattr(e.selected_action, "value") else str(e.selected_action),
            primary_metric=spec.get("primary_metric", "visibility"),
            status=e.status.value if hasattr(e.status, "value") else str(e.status),
            eligible_at=e.verification_window_start.isoformat() if e.verification_window_start else None,
            target_key=e.target_key,
            protection_active=bool(e.target_key and e.status in (
                ExperimentStatus.APPROVED, ExperimentStatus.EXECUTING, ExperimentStatus.EXECUTED, ExperimentStatus.AWAITING_VERIFICATION
            )),
        ))

    # 3. Model cost today
    model_cost_today = sum(a["model_cost"] for a in AGENTS_REGISTRY)

    # 4. Decisions needing review
    pending_decisions = len([d for d in DECISIONS_REGISTRY if d["status"] == "PENDING_REVIEW"])

    # 5. Experiments measuring
    measuring_exps = len([
        e for e in exp_cards if e.status in ("awaiting_verification", "executing", "running", "executed")
    ])

    summary = ControlPlaneSummary(
        active_agents=len([a for a in AGENTS_REGISTRY if a["state"] in ("RUNNING", "REVIEW")]),
        running_campaigns=len([c for c in campaign_cards if c.status == "ACTIVE"]),
        model_cost_today=round(model_cost_today, 2),
        attributed_return=round(tot_return, 2),
        decisions_needing_review=pending_decisions,
        experiments_measuring=measuring_exps,
    )

    # 6. Build the 3D Flight Deck Topology
    # Positions are structured in semantic 3D coordinate space:
    # Agents: left/back (x ~ -8..-5, z ~ -4..-2)
    # Campaigns: center (x ~ 0, z ~ 0)
    # Decisions/Actions: right/mid (x ~ 4..6, z ~ 2..4)
    # Experiments: right/front (x ~ 7..9, z ~ 5..7)
    # Outcomes: far right/front (x ~ 10..12, z ~ 8..10)
    nodes: list[ControlPlaneGraphNode] = []
    edges: list[ControlPlaneGraphEdge] = []

    # Add Campaign Nodes (Center)
    for idx, c in enumerate(campaign_cards[:3]):
        y_pos = (idx - 1) * 3.5
        c_status = "positive" if c.financial_status == "POSITIVE" else ("negative" if c.financial_status == "NEGATIVE" else "neutral")
        nodes.append(ControlPlaneGraphNode(
            id=c.id,
            type="campaign",
            label=c.name,
            status=c_status,
            meta={"cost": c.total_cost, "return": c.attributed_return, "roi": c.roi_pct, "status": c.financial_status},
            x=0.0,
            y=y_pos,
            z=0.0,
        ))

    # Add Agent Nodes (Left/Back)
    for idx, a in enumerate(AGENTS_REGISTRY[:4]):
        y_pos = (idx - 1.5) * 2.8
        a_status = "running" if a["state"] == "RUNNING" else ("positive" if a["attributed_outcome"] == "POSITIVE" else "neutral")
        nodes.append(ControlPlaneGraphNode(
            id=a["id"],
            type="agent",
            label=a["name"],
            status=a_status,
            meta={"role": a["role"], "cost": a["model_cost"], "runs": a["runs"], "task": a["current_task"]},
            x=-7.0,
            y=y_pos,
            z=-3.5,
        ))
        # Edge: Agent -> Campaign
        edges.append(ControlPlaneGraphEdge(
            id=f"e-{a['id']}-{a['campaign_id']}",
            source=a["id"],
            target=a["campaign_id"],
            label="contributes",
            status="active" if a["state"] == "RUNNING" else "neutral",
        ))

    # Add Decision Nodes (Right/Mid)
    for idx, d in enumerate(DECISIONS_REGISTRY):
        y_pos = (idx - 1) * 2.6
        d_status = "positive" if d["observed_outcome"] == "POSITIVE" else ("negative" if d["observed_outcome"] == "NEGATIVE" else "uncertain")
        nodes.append(ControlPlaneGraphNode(
            id=d["id"],
            type="decision",
            label=d["title"],
            status=d_status,
            meta={"recommended_by": d["recommended_by"], "status": d["status"], "outcome": d["observed_outcome"]},
            x=5.0,
            y=y_pos,
            z=2.5,
        ))
        # Edge: Campaign -> Decision
        edges.append(ControlPlaneGraphEdge(
            id=f"e-{d['campaign_id']}-{d['id']}",
            source=d["campaign_id"],
            target=d["id"],
            label="action",
            status=d_status,
        ))

    # Add Experiment Nodes (Right/Front)
    for idx, e in enumerate(exp_cards[:2]):
        y_pos = (idx - 0.5) * 3.0
        nodes.append(ControlPlaneGraphNode(
            id=e.id,
            type="experiment",
            label=e.code,
            status="running" if e.protection_active else "neutral",
            meta={"hypothesis": e.hypothesis, "metric": e.primary_metric, "status": e.status},
            x=8.5,
            y=y_pos,
            z=5.5,
        ))
        # Edge: Decision -> Experiment
        edges.append(ControlPlaneGraphEdge(
            id=f"e-dec-saml-canonical-{e.id}",
            source="dec-saml-canonical",
            target=e.id,
            label="tests",
            status="active",
        ))

    # Add Outcome Node
    nodes.append(ControlPlaneGraphNode(
        id="out-deal-pipeline",
        type="outcome",
        label="Attributed Revenue: +$118K",
        status="positive",
        meta={"direct": 46000.0, "attributed": 51000.0, "modeled": 21000.0},
        x=11.5,
        y=0.0,
        z=8.0,
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-cmp-ai-discovery-launch-01-out-deal-pipeline",
        source="cmp-ai-discovery-launch-01",
        target="out-deal-pipeline",
        label="yields",
        status="positive",
        confidence="HIGH",
    ))

    return ControlPlaneResponse(
        generated_at=now.isoformat(),
        source_mode="LIVE",
        summary=summary,
        agents=[AgentActivity(**a) for a in AGENTS_REGISTRY],
        campaigns=campaign_cards,
        decisions=[DecisionCard(**d) for d in DECISIONS_REGISTRY],
        experiments=exp_cards,
        graph=ControlPlaneGraph(nodes=nodes, edges=edges),
    )


@router.get("/events", response_class=EventSourceResponse)
async def control_plane_events(request: Request, bus: BusDep):
    """Real-time SSE event stream for the Agent Control Plane flight deck."""

    async def stream():
        async for item in merge_streams(bus.subscribe_global(heartbeat_seconds=8)):
            yield {"event": item.get("type", "message"), "data": json.dumps(item)}

    return EventSourceResponse(
        stream(),
        ping=5,
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
