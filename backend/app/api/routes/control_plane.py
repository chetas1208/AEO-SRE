"""Control Plane flight deck API routes: Aggregated live view of Agents, Campaigns, Decisions, Experiments, and Outcomes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import BusDep, SessionDep
from app.api.routes.campaigns import CAMPAIGNS_DB, _sync_custom_from_disk
from app.domain.enums import ExperimentStatus
from app.graph.sse import graph_projected_events, merge_streams
from app.models.core import Incident
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
from app.services.campaign_profound import live_profound_overlay, merge_profound_impact
from app.services.control_plane_live import (
    build_live_agent_registry,
    build_live_control_graph,
    build_live_decisions,
)
from app.services.campaign_economics import enrich_campaign_financials
from app.services.live_surface import iter_public_campaigns, live_surface_enabled

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
    now = datetime.now(UTC)
    _sync_custom_from_disk()
    live_surface = live_surface_enabled()
    agents_registry = (
        await build_live_agent_registry(session)
        if live_surface
        else AGENTS_REGISTRY
    )
    profound_live = await live_profound_overlay(session)

    # 1. Campaigns summary & financial cards
    campaign_cards: list[CampaignFinancialCard] = []
    tot_cost = 0.0
    tot_return = 0.0

    for c in iter_public_campaigns(CAMPAIGNS_DB):
        row = enrich_campaign_financials(
            merge_profound_impact(dict(c), profound_live),
            overlay=profound_live,
            force=False,
        )
        cost = float(row.get("total_cost", 0.0))
        ret = row.get("attributed_return")
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
            id=row["id"],
            name=row["name"],
            status=row.get("status", "ACTIVE"),
            total_cost=cost,
            attributed_return=ret_val,
            net_return=net,
            roi_pct=(roi * 100.0) if roi is not None else None,
            financial_status=fin_status,
            measurement_confidence=row.get("measurement_confidence", "MEDIUM"),
            return_source="ATTRIBUTED",
            primary_channel=row.get("primary_channel", "Search LLMs & Docs"),
            active_agents_count=len([a for a in agents_registry if a.get("campaign_id") == row["id"]]),
            active_experiments_count=0,  # filled below from DB
        ))

    # Live experiment counts per campaign_id (from incident context + proposed_change)
    campaign_exp_counts: dict[str, int] = {}
    all_exps = (await session.execute(select(Experiment))).scalars().all()
    active_statuses = {
        ExperimentStatus.PROPOSED.value,
        ExperimentStatus.APPROVED.value,
        ExperimentStatus.EXECUTING.value,
        ExperimentStatus.EXECUTED.value,
        ExperimentStatus.AWAITING_VERIFICATION.value,
    }
    for e in all_exps:
        st = e.status.value if hasattr(e.status, "value") else str(e.status)
        if st not in active_statuses:
            continue
        inc_row = await session.get(Incident, e.incident_id) if e.incident_id else None
        cid = None
        if inc_row and isinstance(inc_row.context, dict):
            cid = inc_row.context.get("campaign_id")
        if not cid and e.intervention_id:
            from app.models.interventions import Intervention

            iv = await session.get(Intervention, e.intervention_id)
            if iv and isinstance(iv.proposed_change, dict):
                cid = iv.proposed_change.get("campaign_id")
        if cid:
            campaign_exp_counts[str(cid)] = campaign_exp_counts.get(str(cid), 0) + 1
    for card in campaign_cards:
        card.active_experiments_count = campaign_exp_counts.get(card.id, 0)

    # 2. Query actual Experiments from DB
    exp_rows = (await session.execute(
        select(Experiment).order_by(Experiment.created_at.desc()).limit(10)
    )).scalars().all()

    exp_cards: list[ExperimentControlCard] = []
    experiment_campaign_ids: dict[str, str] = {}
    for e in exp_rows:
        spec = e.spec if isinstance(e.spec, dict) else {}
        exp_cid: str | None = None
        inc_row = await session.get(Incident, e.incident_id) if e.incident_id else None
        if inc_row and isinstance(inc_row.context, dict):
            exp_cid = inc_row.context.get("campaign_id")
        if not exp_cid and e.intervention_id:
            from app.models.interventions import Intervention

            iv = await session.get(Intervention, e.intervention_id)
            if iv and isinstance(iv.proposed_change, dict):
                exp_cid = iv.proposed_change.get("campaign_id")
        if exp_cid:
            experiment_campaign_ids[str(e.id)] = str(exp_cid)

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
    model_cost_today = sum(a.get("model_cost", 0) for a in agents_registry)

    # 4. Decisions with Laya non-autoregressive evaluation
    from app.intelligence.laya.runtime import get_laya_runtime
    laya_rt = get_laya_runtime()

    enriched_decisions: list[DecisionCard] = []
    if live_surface:
        for card in await build_live_decisions(session):
            ctx = {
                "action_type": card.action_type,
                "risk": "medium",
                "claims_count": 1,
                "campaign_roi": 0.0,
                "active_experiment_collision": False,
            }
            laya_eval = laya_rt.evaluate_decision(action_name=card.title, context=ctx)
            enriched_decisions.append(
                card.model_copy(
                    update={
                        "decision_source": "LAYA",
                        "laya_distribution": laya_eval["distribution"],
                        "laya_calibrated_confidence": laya_eval["calibrated_confidence"],
                        "laya_model": laya_eval["model_version"],
                        "policy_mode": laya_eval["policy_mode"],
                        "risk_score": laya_eval["risk_score"],
                    }
                )
            )
    else:
        for d in DECISIONS_REGISTRY:
            ctx = {
                "action_type": d.get("action_type", "update"),
                "risk": "low" if d["id"] == "dec-saml-canonical" else ("medium" if d["id"] == "dec-scim-jsonld" else "high"),
                "claims_count": 3 if d["id"] == "dec-saml-canonical" else 1,
                "campaign_roi": 1.76 if d["id"] == "dec-saml-canonical" else 0.8,
                "active_experiment_collision": d["id"] == "dec-legacy-deprecate",
            }
            laya_eval = laya_rt.evaluate_decision(action_name=d["title"], context=ctx)

            enriched_decisions.append(DecisionCard(
                id=d["id"],
                title=d["title"],
                recommended_by=d["recommended_by"],
                campaign_id=d["campaign_id"],
                campaign_name=d["campaign_name"],
                action_type=d["action_type"],
                policy_version=d["policy_version"],
                status=d["status"],
                observed_outcome=d["observed_outcome"],
                context=d["context"],
                cost=d["cost"],
                created_at=d["created_at"],
                decision_source="LAYA",
                laya_distribution=laya_eval["distribution"],
                laya_calibrated_confidence=laya_eval["calibrated_confidence"],
                laya_model=laya_eval["model_version"],
                policy_mode=laya_eval["policy_mode"],
                risk_score=laya_eval["risk_score"],
            ))

    pending_decisions = len([d for d in enriched_decisions if d.status == "PENDING_REVIEW"])

    # 5. Experiments measuring
    measuring_statuses = {
        ExperimentStatus.APPROVED.value,
        ExperimentStatus.EXECUTING.value,
        ExperimentStatus.EXECUTED.value,
        ExperimentStatus.AWAITING_VERIFICATION.value,
    }
    measuring_exps = len([e for e in exp_cards if e.status in measuring_statuses])

    total_agent_runs = sum(int(a.get("runs") or 0) for a in agents_registry)
    decision_cost_total = round(sum(float(d.cost or 0) for d in enriched_decisions), 2)
    net_return_val = (tot_return - tot_cost) if tot_return else None
    blended_roi = (
        ((tot_return - tot_cost) / tot_cost * 100.0) if tot_return and tot_cost > 0 else None
    )

    summary = ControlPlaneSummary(
        active_agents=len([a for a in agents_registry if a.get("state") in ("RUNNING", "REVIEW")]),
        running_campaigns=len([c for c in campaign_cards if c.status == "ACTIVE"]),
        model_cost_today=round(model_cost_today, 2),
        attributed_return=round(tot_return, 2),
        decisions_needing_review=pending_decisions,
        experiments_measuring=measuring_exps,
        total_spend=round(tot_cost, 2),
        net_return=round(net_return_val, 2) if net_return_val is not None else None,
        blended_roi_pct=round(blended_roi, 2) if blended_roi is not None else None,
        total_agent_runs=total_agent_runs,
        decision_cost_total=decision_cost_total,
    )

    if live_surface:
        graph = build_live_control_graph(
            agents=agents_registry,
            campaigns=campaign_cards,
            decisions=enriched_decisions,
            experiments=exp_cards,
            profound_live=profound_live,
            experiment_campaign_ids=experiment_campaign_ids,
        )
        return ControlPlaneResponse(
            generated_at=now.isoformat(),
            source_mode="LIVE",
            data_provenance="LIVE",
            summary=summary,
            agents=[AgentActivity(**a) for a in agents_registry],
            campaigns=campaign_cards,
            decisions=enriched_decisions,
            experiments=exp_cards,
            graph=graph,
        )

    # 6. Build the 3D Flight Deck Topology (fixture demo — only without PROFOUND_API_KEY)
    # Semantic 3D coordinate space:
    # Signals: left / back (x ~ -11.0, z ~ -4.0)
    # Agents: left / mid-back (x ~ -7.0, z ~ -2.5)
    # Campaigns: center gravity (x ~ 0.0, z ~ 0.0)
    # Costs: amber satellites (x ~ -2.0, y ~ -3.0, z ~ -0.5)
    # Decisions: mid right (x ~ 4.5, z ~ 2.0)
    # Laya Gate: decision marker (x ~ 6.0, z ~ 3.0)
    # Experiments: right (x ~ 8.5, z ~ 4.5)
    # Observations & Outcomes: far right / front (x ~ 11.5, z ~ 6.5)
    # Rewards: learning return loop (x ~ 9.0, y ~ -4.0, z ~ 4.0)
    nodes: list[ControlPlaneGraphNode] = []
    edges: list[ControlPlaneGraphEdge] = []

    # 6.1 Signals (Perception & Search Gaps)
    nodes.append(ControlPlaneGraphNode(
        id="sig-profound-saml",
        type="signal",
        label="Profound Gap: SAML Omission",
        status="running",
        meta={"source": "Profound Perception Engine", "query_cluster": "Enterprise SAML 2.0 SCIM Auth", "gap_severity": "HIGH"},
        x=-11.0,
        y=-2.0,
        z=-4.0,
    ))
    nodes.append(ControlPlaneGraphNode(
        id="sig-muse-scim-gap",
        type="signal",
        label="Muse Intent: SCIM Provisioning",
        status="running",
        meta={"source": "Muse Buyer Agent Intent", "intent_type": "Identity Provider Integration", "match_confidence": 0.88},
        x=-11.0,
        y=2.0,
        z=-4.0,
    ))

    # 6.2 Agent Nodes
    for idx, a in enumerate(agents_registry[:4]):
        y_pos = (idx - 1.5) * 2.6
        a_status = "running" if a["state"] == "RUNNING" else ("positive" if a["attributed_outcome"] == "POSITIVE" else "neutral")
        nodes.append(ControlPlaneGraphNode(
            id=a["id"],
            type="agent",
            label=a["name"],
            status=a_status,
            meta={"role": a["role"], "cost": a["model_cost"], "runs": a["runs"], "task": a["current_task"]},
            x=-7.0,
            y=y_pos,
            z=-2.5,
        ))

    # Edges: Signals -> Agents
    edges.append(ControlPlaneGraphEdge(
        id="e-sig-profound-agt-citation",
        source="sig-profound-saml",
        target="agt-citation-recovery",
        label="OBSERVED",
        status="active",
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-sig-muse-agt-claim",
        source="sig-muse-scim-gap",
        target="agt-claim-verifier",
        label="OBSERVED",
        status="active",
    ))

    # 6.3 Campaign Nodes (Center Gravity)
    for idx, c in enumerate(campaign_cards[:4]):
        y_pos = (idx - 1.5) * 3.2
        c_status = "positive" if c.financial_status == "POSITIVE" else ("negative" if c.financial_status == "NEGATIVE" else "uncertain")
        nodes.append(ControlPlaneGraphNode(
            id=c.id,
            type="campaign",
            label=c.name,
            status=c_status,
            meta={"cost": c.total_cost, "return": c.attributed_return, "roi": c.roi_pct, "status": c.financial_status, "confidence": c.measurement_confidence},
            x=0.0,
            y=y_pos,
            z=0.0,
        ))

    # Edges: Agents -> Campaigns
    for a in agents_registry[:4]:
        if any(c.id == a["campaign_id"] for c in campaign_cards):
            edges.append(ControlPlaneGraphEdge(
                id=f"e-{a['id']}-{a['campaign_id']}",
                source=a["id"],
                target=a["campaign_id"],
                label="WORKING_ON",
                status="active" if a["state"] == "RUNNING" else "neutral",
            ))

    # 6.4 Cost Satellites
    nodes.append(ControlPlaneGraphNode(
        id="cost-model-tokens",
        type="cost",
        label="Model Cost: $142.50",
        status="neutral",
        meta={"category": "Model APIs", "amount": 142.50, "router": "Haiku 4.5 first"},
        x=-2.2,
        y=-3.2,
        z=-0.8,
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-cost-model-agt-citation",
        source="cost-model-tokens",
        target="agt-citation-recovery",
        label="COST_OF",
        status="neutral",
    ))

    # 6.5 Decision Nodes & Laya Gate
    for idx, d in enumerate(enriched_decisions):
        y_pos = (idx - 1) * 2.5
        d_status = "positive" if d.observed_outcome == "POSITIVE" else ("negative" if d.observed_outcome == "NEGATIVE" else "uncertain")
        nodes.append(ControlPlaneGraphNode(
            id=d.id,
            type="decision",
            label=d.title,
            status=d_status,
            meta={
                "recommended_by": d.recommended_by,
                "status": d.status,
                "outcome": d.observed_outcome,
                "laya_model": d.laya_model,
                "laya_distribution": d.laya_distribution,
                "laya_confidence": d.laya_calibrated_confidence,
                "policy_mode": d.policy_mode,
            },
            x=4.5,
            y=y_pos,
            z=2.0,
        ))
        # Edge: Campaign -> Decision
        if any(c.id == d.campaign_id for c in campaign_cards):
            edges.append(ControlPlaneGraphEdge(
                id=f"e-{d.campaign_id}-{d.id}",
                source=d.campaign_id,
                target=d.id,
                label="ACTION",
                status=d_status,
            ))

    # Dedicated Laya Typed Decision Gate Node
    nodes.append(ControlPlaneGraphNode(
        id="laya-decision-gate",
        type="laya_decision",
        label="Laya Decision: 74% ALLOW",
        status="positive",
        meta={
            "model": "convaiinnovations/laya-typed-decisions",
            "choice": "ALLOW",
            "distribution": {"ALLOW": 0.74, "DELAY": 0.14, "REVIEW": 0.09, "BLOCK": 0.03},
            "calibrated_confidence": 0.74,
            "policy_mode": "SHADOW",
            "eval": "Non-autoregressive typed probability gate",
        },
        x=6.2,
        y=-1.5,
        z=3.2,
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-dec-saml-laya-gate",
        source="dec-saml-canonical",
        target="laya-decision-gate",
        label="EVALUATED_BY",
        status="positive",
    ))

    # 6.6 Experiment Nodes (Right/Front)
    for idx, e in enumerate(exp_cards[:3]):
        y_pos = (idx - 1.0) * 2.8
        nodes.append(ControlPlaneGraphNode(
            id=e.id,
            type="experiment",
            label=e.code,
            status="running" if e.protection_active else "neutral",
            meta={"hypothesis": e.hypothesis, "metric": e.primary_metric, "status": e.status, "protection": e.protection_active},
            x=8.5,
            y=y_pos,
            z=4.5,
        ))

    # Edges: Decision / Laya -> Experiments (each experiment wired into the process)
    for idx, exp in enumerate(exp_cards[:3]):
        if idx == 0:
            src = "laya-decision-gate"
        elif enriched_decisions:
            src = enriched_decisions[min(idx, len(enriched_decisions) - 1)].id
        else:
            continue
        edges.append(ControlPlaneGraphEdge(
            id=f"e-dec-exp-{src}-{exp.id}",
            source=src,
            target=exp.id,
            label="VERIFIES",
            status="active",
        ))

    # 6.7 Outcomes & Returns (Far Right / Front)
    nodes.append(ControlPlaneGraphNode(
        id="out-deal-pipeline",
        type="outcome",
        label="Attributed Revenue: +$118K",
        status="positive",
        meta={"direct": 46000.0, "attributed": 51000.0, "modeled": 21000.0, "confidence": "HIGH"},
        x=11.5,
        y=-1.0,
        z=6.5,
    ))
    nodes.append(ControlPlaneGraphNode(
        id="out-visibility-lift",
        type="outcome",
        label="Profound Lift: +9.2pp",
        status="positive",
        meta={"metric": "AI Search Visibility", "pre": 14.2, "post": 23.4, "change": 9.2},
        x=11.5,
        y=1.8,
        z=6.5,
    ))

    # Edge: Experiments -> Visibility Lift -> Deal Pipeline
    for exp in exp_cards[:3]:
        edges.append(ControlPlaneGraphEdge(
            id=f"e-{exp.id}-out-visibility",
            source=exp.id,
            target="out-visibility-lift",
            label="MEASURES",
            status="positive",
        ))
    edges.append(ControlPlaneGraphEdge(
        id="e-visibility-deal-pipeline",
        source="out-visibility-lift",
        target="out-deal-pipeline",
        label="RESULTED_IN",
        status="positive",
        confidence="HIGH",
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-cmp-ai-discovery-launch-01-out-deal-pipeline",
        source="cmp-ai-discovery-launch-01",
        target="out-deal-pipeline",
        label="YIELDS",
        status="positive",
        confidence="HIGH",
    ))

    # 6.8 Reward & Policy Learning Node
    nodes.append(ControlPlaneGraphNode(
        id="rew-saml-causal",
        type="reward",
        label="Policy Reward: +0.74",
        status="positive",
        meta={"causal_lift": 9.2, "reward_score": 0.74, "target_policy": "v0.3.1", "bandit_updated": True},
        x=8.5,
        y=-3.8,
        z=3.5,
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-out-deal-rew-saml",
        source="out-deal-pipeline",
        target="rew-saml-causal",
        label="REWARDED",
        status="positive",
    ))
    edges.append(ControlPlaneGraphEdge(
        id="e-rew-saml-laya-gate",
        source="rew-saml-causal",
        target="laya-decision-gate",
        label="LEARNED_FROM",
        status="active",
    ))

    return ControlPlaneResponse(
        generated_at=now.isoformat(),
        source_mode="LIVE",
        data_provenance="FIXTURE",
        summary=summary,
        agents=[AgentActivity(**a) for a in agents_registry],
        campaigns=campaign_cards,
        decisions=enriched_decisions,
        experiments=exp_cards,
        graph=ControlPlaneGraph(nodes=nodes, edges=edges),
    )



@router.get("/events", response_class=EventSourceResponse)
async def control_plane_events(request: Request, bus: BusDep):
    """Real-time SSE event stream for the Agent Control Plane flight deck."""

    async def stream():
        async for item in merge_streams(bus.subscribe_global(heartbeat_seconds=8), graph_projected_events()):
            yield {"event": item.get("type", "message"), "data": json.dumps(item)}

    return EventSourceResponse(
        stream(),
        ping=5,
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
