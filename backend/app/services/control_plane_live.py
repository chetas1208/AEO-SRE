"""Live Profound agent activity for the control plane (replaces static registry when API key is set)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.campaigns import CAMPAIGNS_DB, _sync_custom_from_disk
from app.domain.enums import ExperimentStatus
from app.models.changeguard import ChangeCheck, ChangeSet
from app.models.interventions import Experiment, Intervention
from app.schemas.control_plane import (
    CampaignFinancialCard,
    ControlPlaneGraph,
    ControlPlaneGraphEdge,
    ControlPlaneGraphNode,
    DecisionCard,
    ExperimentControlCard,
)
from app.services.live_surface import iter_public_campaigns
from app.services.profound_agents import LIVE_RUN_META, list_profound_agents, map_run_state


async def build_live_agent_registry(session: AsyncSession) -> list[dict[str, Any]]:
    """Aggregate LIVE Profound agent runs from campaigns + experiments."""
    _sync_custom_from_disk()
    catalog = {a["id"]: a for a in (await list_profound_agents(limit=100)).get("agents") or []}
    by_agent: dict[str, dict[str, Any]] = {}

    def ingest_run(
        *,
        agent_id: str,
        run: dict[str, Any],
        campaign_id: str,
        campaign_name: str,
        task: str,
    ) -> None:
        rid = str(run.get("run_id") or run.get("id") or "")
        key = f"{agent_id}:{rid or campaign_id}"
        cat = catalog.get(agent_id) or {}
        name = str(cat.get("name") or run.get("agent_name") or agent_id)
        state = map_run_state(run.get("status"))
        row = by_agent.get(key)
        if row is None:
            by_agent[key] = {
                "id": rid or f"live-{agent_id[:8]}",
                "name": name,
                "role": "Profound Agent (LIVE)",
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "current_task": task[:240],
                "runs": 1,
                "model_cost": 0.0,
                "total_cost": 0.0,
                "outputs_produced": len(run.get("outputs") or {}) if isinstance(run.get("outputs"), dict) else 0,
                "outputs_accepted": 0,
                "attributed_outcome": "NOT_MEASURABLE",
                "state": state,
                "last_active_at": run.get("finished_at") or run.get("started_at") or datetime.now(UTC).isoformat(),
                "source": LIVE_RUN_META["source"],
                "source_mode": LIVE_RUN_META["source_mode"],
                "profound_agent_id": agent_id,
                "profound_run_id": rid or None,
            }
        else:
            row["runs"] = int(row.get("runs") or 0) + 1

    for c in iter_public_campaigns(CAMPAIGNS_DB):
        gen = c.get("profound_generation") or {}
        for run in gen.get("runs") or []:
            if not isinstance(run, dict):
                continue
            aid = str(run.get("agent_id") or "")
            if not aid:
                continue
            ingest_run(
                agent_id=aid,
                run=run,
                campaign_id=str(c.get("id") or ""),
                campaign_name=str(c.get("name") or "Campaign"),
                task=str(c.get("objective") or c.get("name") or "Campaign generation"),
            )

    q = select(Experiment.id, Experiment.proposed_change, Experiment.reason).where(
        Experiment.proposed_change.isnot(None)
    )
    for exp_id, pc, reason in (await session.execute(q)).all():
        if not isinstance(pc, dict):
            continue
        gen = pc.get("profound_generation") or {}
        cid = (pc.get("campaign_id") or "experiment") if isinstance(pc, dict) else "experiment"
        for run in gen.get("runs") or []:
            if not isinstance(run, dict):
                continue
            aid = str(run.get("agent_id") or "")
            if not aid:
                continue
            ingest_run(
                agent_id=aid,
                run=run,
                campaign_id=str(cid),
                campaign_name=f"Experiment {str(exp_id)[:8]}",
                task=str(reason or "Experiment generation")[:240],
            )

    # Catalog agents with no runs yet still appear as LIVE idle (not demo fixtures).
    for aid, cat in catalog.items():
        if any(r.get("profound_agent_id") == aid for r in by_agent.values()):
            continue
        by_agent[f"catalog:{aid}"] = {
            "id": aid,
            "name": str(cat.get("name") or aid),
            "role": "Profound Agent (LIVE)",
            "campaign_id": "",
            "campaign_name": "Profound Command Center",
            "current_task": f"Status: {cat.get('status') or 'unknown'} — ready to run",
            "runs": 0,
            "model_cost": 0.0,
            "total_cost": 0.0,
            "outputs_produced": 0,
            "outputs_accepted": 0,
            "attributed_outcome": "NOT_MEASURABLE",
            "state": "WAITING",
            "last_active_at": cat.get("created_at") or datetime.now(UTC).isoformat(),
            "source": LIVE_RUN_META["source"],
            "source_mode": LIVE_RUN_META["source_mode"],
            "profound_agent_id": aid,
            "profound_run_id": None,
        }

    return list(by_agent.values())


async def build_live_decisions(session: AsyncSession) -> list[DecisionCard]:
    """Decisions from Change Guard checks and open experiments (no fixture registry)."""
    cards: list[DecisionCard] = []
    rows = (
        await session.execute(
            select(ChangeSet, ChangeCheck)
            .join(ChangeCheck, ChangeCheck.change_set_id == ChangeSet.id)
            .order_by(ChangeCheck.created_at.desc())
            .limit(12)
        )
    ).all()
    for cs, chk in rows:
        status_map = {"ALLOW": "APPROVED", "DELAY": "PENDING_REVIEW", "REVIEW": "PENDING_REVIEW", "BLOCK": "REJECTED"}
        cards.append(
            DecisionCard(
                id=str(chk.id),
                title=(cs.reason or f"{cs.action_type} on {cs.target_key or 'target'}")[:256],
                recommended_by=cs.agent_name or "Change Guard agent",
                campaign_id="",
                campaign_name="Live campaign",
                action_type=cs.action_type or "update",
                policy_version=chk.guard_version or "live",
                status=status_map.get(chk.decision, "PENDING_REVIEW"),
                observed_outcome="PENDING",
                context=f"{cs.source_mode} · {chk.decision} · {len(chk.findings or [])} findings",
                cost=0.0,
                created_at=chk.created_at.isoformat() if chk.created_at else datetime.now(UTC).isoformat(),
                decision_source="RULE",
            )
        )

    exp_rows = (
        await session.execute(
            select(Experiment)
            .where(Experiment.status.in_((ExperimentStatus.PROPOSED, ExperimentStatus.APPROVED)))
            .order_by(Experiment.created_at.desc())
            .limit(6)
        )
    ).scalars().all()
    for exp in exp_rows:
        iv = await session.get(Intervention, exp.intervention_id) if exp.intervention_id else None
        cid = ""
        if iv and isinstance(iv.proposed_change, dict):
            cid = str(iv.proposed_change.get("campaign_id") or "")
        cards.append(
            DecisionCard(
                id=f"exp-{exp.id}",
                title=(exp.reason or exp.code)[:256],
                recommended_by="Experiment engine",
                campaign_id=cid,
                campaign_name="Live experiment",
                action_type=exp.selected_action.value if hasattr(exp.selected_action, "value") else str(exp.selected_action),
                policy_version="live",
                status="PENDING_REVIEW" if exp.status == ExperimentStatus.PROPOSED else "APPROVED",
                observed_outcome="PENDING",
                context="LIVE experiment · Profound verification window",
                cost=0.0,
                created_at=exp.created_at.isoformat() if exp.created_at else datetime.now(UTC).isoformat(),
                decision_source="HUMAN",
            )
        )
    return cards


def build_live_control_graph(
    *,
    agents: list[dict[str, Any]],
    campaigns: list[CampaignFinancialCard],
    decisions: list[DecisionCard],
    experiments: list[ExperimentControlCard],
    profound_live: dict[str, Any],
) -> ControlPlaneGraph:
    """3D topology from real entities + Profound overlay only."""
    nodes: list[ControlPlaneGraphNode] = []
    edges: list[ControlPlaneGraphEdge] = []

    if profound_live.get("status") == "OK":
        vis = profound_live.get("metrics", {}).get("visibility")
        delta = (profound_live.get("delta_7d_pp") or {}).get("visibility")
        nodes.append(
            ControlPlaneGraphNode(
                id="sig-profound-live",
                type="signal",
                label=f"Profound visibility ({profound_live.get('signal_count', 0)} signals)",
                status="running",
                meta={
                    "source": "PROFOUND",
                    "source_mode": "LIVE",
                    "visibility": vis,
                    "delta_7d_pp": delta,
                    "effectiveness": profound_live.get("campaign_effectiveness"),
                },
                x=-11.0,
                y=0.0,
                z=-4.0,
            )
        )

    for idx, a in enumerate(agents[:8]):
        y_pos = (idx - 3.5) * 2.2
        st = "running" if a.get("state") in ("RUNNING", "REVIEW", "WAITING") else "neutral"
        nodes.append(
            ControlPlaneGraphNode(
                id=str(a["id"]),
                type="agent",
                label=str(a["name"]),
                status=st,
                meta={
                    **{k: a[k] for k in ("role", "runs", "current_task", "source_mode", "profound_run_id") if k in a},
                    "source_mode": a.get("source_mode", "LIVE"),
                },
                x=-7.0,
                y=y_pos,
                z=-2.5,
            )
        )
        if nodes and nodes[0].id == "sig-profound-live":
            edges.append(
                ControlPlaneGraphEdge(
                    id=f"e-sig-{a['id']}",
                    source="sig-profound-live",
                    target=str(a["id"]),
                    label="OBSERVED",
                    status="active",
                )
            )

    for idx, c in enumerate(campaigns[:8]):
        y_pos = (idx - 3.5) * 2.8
        nodes.append(
            ControlPlaneGraphNode(
                id=c.id,
                type="campaign",
                label=c.name,
                status="positive" if c.financial_status == "POSITIVE" else "uncertain",
                meta={"cost": c.total_cost, "return": c.attributed_return, "source_mode": "LIVE"},
                x=0.0,
                y=y_pos,
                z=0.0,
            )
        )

    for a in agents[:8]:
        cid = a.get("campaign_id")
        if cid and any(c.id == cid for c in campaigns):
            edges.append(
                ControlPlaneGraphEdge(
                    id=f"e-agent-{a['id']}-{cid}",
                    source=str(a["id"]),
                    target=str(cid),
                    label="WORKING_ON",
                    status="active",
                )
            )

    for idx, d in enumerate(decisions[:6]):
        y_pos = (idx - 2.5) * 2.4
        nodes.append(
            ControlPlaneGraphNode(
                id=d.id,
                type="decision",
                label=d.title[:80],
                status="uncertain" if d.status == "PENDING_REVIEW" else "positive",
                meta={"status": d.status, "source_mode": "LIVE", "recommended_by": d.recommended_by},
                x=4.5,
                y=y_pos,
                z=2.0,
            )
        )
        if d.campaign_id and any(c.id == d.campaign_id for c in campaigns):
            edges.append(
                ControlPlaneGraphEdge(
                    id=f"e-cmp-{d.campaign_id}-{d.id}",
                    source=d.campaign_id,
                    target=d.id,
                    label="ACTION",
                    status="active",
                )
            )

    for idx, e in enumerate(experiments[:6]):
        y_pos = (idx - 2.5) * 2.6
        nodes.append(
            ControlPlaneGraphNode(
                id=e.id,
                type="experiment",
                label=e.code,
                status="running" if e.protection_active else "neutral",
                meta={"hypothesis": e.hypothesis[:120], "status": e.status, "source_mode": "LIVE"},
                x=8.5,
                y=y_pos,
                z=4.5,
            )
        )

    delta = (profound_live.get("delta_7d_pp") or {}).get("visibility")
    if delta is not None:
        nodes.append(
            ControlPlaneGraphNode(
                id="out-profound-live",
                type="outcome",
                label=f"Profound Δ visibility: {delta:+.2f}pp",
                status="positive" if (delta or 0) >= 0 else "negative",
                meta={"source": "PROFOUND", "source_mode": "LIVE", "delta_pp": delta},
                x=11.5,
                y=0.0,
                z=6.5,
            )
        )
        if experiments:
            edges.append(
                ControlPlaneGraphEdge(
                    id=f"e-exp-out-{experiments[0].id}",
                    source=experiments[0].id,
                    target="out-profound-live",
                    label="MEASURED_BY",
                    status="active",
                )
            )

    return ControlPlaneGraph(nodes=nodes, edges=edges)
