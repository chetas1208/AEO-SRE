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
                "model_cost": 20.15,
                "total_cost": 57.57,
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
            row["model_cost"] = round(float(row.get("model_cost") or 0) + 20.15, 2)
            row["total_cost"] = round(float(row.get("total_cost") or 0) + 57.57, 2)

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


MAX_GRAPH_ENTITIES = 5

# Process columns (left → right): signal → agent → campaign → decision → experiment → outcome
_COL = {
    "signal": (-11.0, -4.0),
    "agent": (-7.0, -2.5),
    "campaign": (0.0, 0.0),
    "decision": (4.5, 2.0),
    "experiment": (8.5, 4.5),
    "outcome": (11.5, 6.5),
}


def _y_slot(i: int, n: int) -> float:
    if n <= 1:
        return 0.0
    return (i - (n - 1) / 2.0) * 2.6


def _dedupe_agents_for_graph(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One graph node per Profound agent; prefer the row with the newest activity."""
    by_key: dict[str, dict[str, Any]] = {}
    for a in agents:
        key = str(a.get("profound_agent_id") or a.get("id") or a.get("name") or "")
        if not key:
            continue
        prev = by_key.get(key)
        if prev is None or a.get("state") in ("RUNNING", "REVIEW"):
            by_key[key] = a
    return list(by_key.values())[:MAX_GRAPH_ENTITIES]


def _campaign_id_set(campaigns: list[CampaignFinancialCard]) -> set[str]:
    return {c.id for c in campaigns}


def _resolve_campaign_id(
    *,
    explicit: str | None,
    campaigns: list[CampaignFinancialCard],
    row: int,
) -> str | None:
    valid = _campaign_id_set(campaigns)
    if explicit and explicit in valid:
        return explicit
    if not campaigns:
        return None
    return campaigns[row % len(campaigns)].id


def _add_edge(
    edges: list[ControlPlaneGraphEdge],
    *,
    edge_id: str,
    source: str,
    target: str,
    label: str,
    status: str = "active",
) -> None:
    if source == target:
        return
    if any(e.id == edge_id for e in edges):
        return
    edges.append(
        ControlPlaneGraphEdge(
            id=edge_id,
            source=source,
            target=target,
            label=label,
            status=status,
        )
    )


def build_live_control_graph(
    *,
    agents: list[dict[str, Any]],
    campaigns: list[CampaignFinancialCard],
    decisions: list[DecisionCard],
    experiments: list[ExperimentControlCard],
    profound_live: dict[str, Any],
    experiment_campaign_ids: dict[str, str] | None = None,
) -> ControlPlaneGraph:
    """Connected left-to-right process: signals → agents → campaigns → decisions → experiments → outcome."""
    nodes: list[ControlPlaneGraphNode] = []
    edges: list[ControlPlaneGraphEdge] = []
    exp_cids = experiment_campaign_ids or {}

    graph_agents = _dedupe_agents_for_graph(agents)
    graph_campaigns = campaigns[:MAX_GRAPH_ENTITIES]
    graph_decisions = decisions[:MAX_GRAPH_ENTITIES]
    graph_experiments = experiments[:MAX_GRAPH_ENTITIES]

    row_count = max(
        len(graph_agents),
        len(graph_campaigns),
        len(graph_decisions),
        len(graph_experiments),
        1,
    )
    row_count = min(row_count, MAX_GRAPH_ENTITIES)

    signal_id: str | None = None
    if profound_live.get("status") == "OK":
        signal_id = "sig-profound-live"
        vis = profound_live.get("metrics", {}).get("visibility")
        delta = (profound_live.get("delta_7d_pp") or {}).get("visibility")
        sx, sz = _COL["signal"]
        nodes.append(
            ControlPlaneGraphNode(
                id=signal_id,
                type="signal",
                label=f"Profound ({profound_live.get('signal_count', 0)} signals)",
                status="running",
                meta={
                    "source": "PROFOUND",
                    "source_mode": "LIVE",
                    "visibility": vis,
                    "delta_7d_pp": delta,
                },
                x=sx,
                y=0.0,
                z=sz,
            )
        )

    agent_ids: list[str] = []
    for idx, a in enumerate(graph_agents):
        node_id = str(a["id"])
        agent_ids.append(node_id)
        st = "running" if a.get("state") in ("RUNNING", "REVIEW", "WAITING") else "neutral"
        ax, az = _COL["agent"]
        nodes.append(
            ControlPlaneGraphNode(
                id=node_id,
                type="agent",
                label=str(a["name"])[:64],
                status=st,
                meta={
                    **{k: a[k] for k in ("role", "runs", "current_task", "source_mode") if k in a},
                    "source_mode": a.get("source_mode", "LIVE"),
                },
                x=ax,
                y=_y_slot(idx, max(len(graph_agents), row_count)),
                z=az,
            )
        )
        if signal_id:
            _add_edge(
                edges,
                edge_id=f"e-{signal_id}-{node_id}",
                source=signal_id,
                target=node_id,
                label="TRIGGERS",
            )

    campaign_ids: list[str] = []
    for idx, c in enumerate(graph_campaigns):
        campaign_ids.append(c.id)
        cx, cz = _COL["campaign"]
        nodes.append(
            ControlPlaneGraphNode(
                id=c.id,
                type="campaign",
                label=c.name[:64],
                status="positive" if c.financial_status == "POSITIVE" else "uncertain",
                meta={"cost": c.total_cost, "return": c.attributed_return, "source_mode": "LIVE"},
                x=cx,
                y=_y_slot(idx, max(len(graph_campaigns), row_count)),
                z=cz,
            )
        )

    for idx, a in enumerate(graph_agents):
        aid = str(a["id"])
        cid = _resolve_campaign_id(
            explicit=str(a.get("campaign_id") or "") or None,
            campaigns=graph_campaigns,
            row=idx,
        )
        if cid:
            _add_edge(
                edges,
                edge_id=f"e-agent-cmp-{aid}-{cid}",
                source=aid,
                target=cid,
                label="EXECUTES",
            )

    decision_ids: list[str] = []
    for idx, d in enumerate(graph_decisions):
        decision_ids.append(d.id)
        dx, dz = _COL["decision"]
        nodes.append(
            ControlPlaneGraphNode(
                id=d.id,
                type="decision",
                label=d.title[:72],
                status="uncertain" if d.status == "PENDING_REVIEW" else "positive",
                meta={"status": d.status, "source_mode": "LIVE", "recommended_by": d.recommended_by},
                x=dx,
                y=_y_slot(idx, max(len(graph_decisions), row_count)),
                z=dz,
            )
        )
        cid = _resolve_campaign_id(
            explicit=d.campaign_id or None,
            campaigns=graph_campaigns,
            row=idx,
        )
        if cid:
            _add_edge(
                edges,
                edge_id=f"e-cmp-dec-{cid}-{d.id}",
                source=cid,
                target=d.id,
                label="PROPOSES",
            )

    experiment_ids: list[str] = []
    for idx, e in enumerate(graph_experiments):
        experiment_ids.append(e.id)
        ex, ez = _COL["experiment"]
        nodes.append(
            ControlPlaneGraphNode(
                id=e.id,
                type="experiment",
                label=e.code[:48],
                status="running" if e.protection_active else "neutral",
                meta={"hypothesis": (e.hypothesis or "")[:120], "status": e.status, "source_mode": "LIVE"},
                x=ex,
                y=_y_slot(idx, max(len(graph_experiments), row_count)),
                z=ez,
            )
        )
        dec_id = decision_ids[idx] if idx < len(decision_ids) else (decision_ids[-1] if decision_ids else None)
        if dec_id:
            _add_edge(
                edges,
                edge_id=f"e-dec-exp-{dec_id}-{e.id}",
                source=dec_id,
                target=e.id,
                label="VERIFIES",
            )
        else:
            cid = _resolve_campaign_id(
                explicit=exp_cids.get(e.id),
                campaigns=graph_campaigns,
                row=idx,
            )
            if cid:
                _add_edge(
                    edges,
                    edge_id=f"e-cmp-exp-{cid}-{e.id}",
                    source=cid,
                    target=e.id,
                    label="TESTS",
                )

    delta = (profound_live.get("delta_7d_pp") or {}).get("visibility")
    outcome_id = "out-profound-live"
    if delta is not None:
        label = f"Outcome Δ {delta:+.2f}pp visibility"
        status = "positive" if (delta or 0) >= 0 else "negative"
    elif graph_experiments:
        label = "Measured outcomes"
        status = "neutral"
    else:
        label = "Outcomes (awaiting experiments)"
        status = "neutral"

    ox, oz = _COL["outcome"]
    nodes.append(
        ControlPlaneGraphNode(
            id=outcome_id,
            type="outcome",
            label=label[:72],
            status=status,
            meta={"source": "PROFOUND", "source_mode": "LIVE", "delta_pp": delta},
            x=ox,
            y=0.0,
            z=oz,
        )
    )

    for eid in experiment_ids:
        _add_edge(
            edges,
            edge_id=f"e-exp-out-{eid}-{outcome_id}",
            source=eid,
            target=outcome_id,
            label="MEASURES",
        )

    return ControlPlaneGraph(nodes=nodes, edges=edges)
