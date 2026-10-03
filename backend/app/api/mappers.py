"""ORM -> response mapping for models owned by other agents (A5 interventions/experiments, A9 policy).

Written against the models contract in docs/BUILD_BRIEF.md; attribute access is tolerant so a renamed
optional column degrades to None instead of a 500.
"""

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import adapters
from app.domain.enums import SelectionBasis
from app.models.core import AuditEvent, Incident
from app.schemas.experiments import ExperimentRow, TimelineEntry
from app.schemas.interventions import InterventionOut


def jsonable(v: Any) -> Any:
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set):
        return [jsonable(x) for x in v]
    return v


def row_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    return {c.key: jsonable(getattr(obj, c.key, None)) for c in sa_inspect(obj).mapper.column_attrs}


def g(obj: Any, name: str, default: Any = None) -> Any:
    v = getattr(obj, name, default)
    return v.value if isinstance(v, enum.Enum) else v


async def latest(session: AsyncSession, model: Any, fk: str, value: Any) -> Any | None:
    if model is None or not hasattr(model, fk):
        return None
    stmt = select(model).where(getattr(model, fk) == value)
    order = next(
        (getattr(model, c) for c in ("created_at", "decided_at", "started_at") if hasattr(model, c)), None
    )
    if order is not None:
        stmt = stmt.order_by(order.desc())
    return (await session.execute(stmt.limit(1))).scalars().first()


async def policy_version_label(session: AsyncSession, policy_version_id: Any) -> str | None:
    pm = adapters.policy_models()
    if pm is None or policy_version_id is None or not hasattr(pm, "PolicyVersion"):
        return None
    pv = await session.get(pm.PolicyVersion, policy_version_id)
    return g(pv, "version") if pv else None


async def policy_is_cold(session: AsyncSession, policy_version_id: Any) -> bool:
    """True while the referenced policy version has learned nothing (n_updates == 0): its choices are priors/rules."""
    pm = adapters.policy_models()
    if pm is None or policy_version_id is None or not hasattr(pm, "PolicyVersion"):
        return False
    pv = await session.get(pm.PolicyVersion, policy_version_id)  # identity-map hit after policy_version_label
    return bool(pv is not None and (g(pv, "n_updates") or 0) == 0)


def execution_target(change: dict[str, Any] | None) -> str | None:
    if not isinstance(change, dict):
        return None
    t = change.get("target_url") or change.get("target") or change.get("path") or change.get("url")
    if isinstance(t, dict):
        t = t.get("path") or t.get("url") or t.get("file")
    if not t and isinstance(change.get("files"), list) and change["files"]:
        f = change["files"][0]
        t = f.get("path") if isinstance(f, dict) else f
    return str(t) if t else None


async def _change_guard(session: AsyncSession, row: Any):
    """Stored Change Guard verdict (read-only; None = never checked, rendered as unavailable)."""
    from app.changeguard.service import verdict_for_intervention

    try:
        return await verdict_for_intervention(session, row)
    except Exception:  # noqa: BLE001  - a guard read problem must never break the intervention view
        return None


async def intervention_out(
    session: AsyncSession, row: Any, *, experiments: int | None = None
) -> InterventionOut:
    mod = adapters.interventions_models()
    approval = execution = experiment = None
    if mod is not None:
        approval = await latest(session, getattr(mod, "Approval", None), "intervention_id", row.id)
        execution = await latest(session, getattr(mod, "Execution", None), "intervention_id", row.id)
        experiment = await latest(session, getattr(mod, "Experiment", None), "intervention_id", row.id)
    change = g(row, "proposed_change") or {}
    basis = g(row, "selection_basis")
    pending = bool(
        execution is not None
        and g(execution, "status") == "awaiting_human_execution"
        and g(approval, "status") in ("approved", "modified")
    )
    return InterventionOut(
        id=row.id,
        incident_id=row.incident_id,
        hypothesis_id=g(row, "hypothesis_id"),
        action=str(g(row, "action")),
        title=g(row, "title") or str(g(row, "action")),
        score=g(row, "score"),
        risk=g(row, "risk"),
        reason=g(row, "rationale") or "",
        selected=bool(g(row, "selected", False)),
        selection_basis=basis,
        policy_version=await policy_version_label(session, g(row, "policy_version_id")),
        cold_start=basis == SelectionBasis.COLD_START_PRIOR.value or await policy_is_cold(session, g(row, "policy_version_id")),
        proposed_change=jsonable(change),
        execution_target=execution_target(change),
        executor=(g(execution, "executor") if execution else None)
        or (g(experiment, "executor") if experiment else None)
        or "manual",
        rollback=(change.get("rollback") if isinstance(change, dict) else None),
        observation_window_hours=(
            change.get("observation_window_hours") if isinstance(change, dict) else None
        ),
        approval_status=g(approval, "status", "pending") if approval else "pending",
        approval=row_dict(approval) if approval else None,
        execution=row_dict(execution) if execution else None,
        package=jsonable(g(execution, "package")) if execution else None,
        manual_execution_pending=pending,
        experiment_id=getattr(experiment, "id", None),
        based_on_experiments=experiments,
        change_guard=await _change_guard(session, row),
    )


# ---- experiments ----------------------------------------------------------------------------------------
DISPLAY_STATUS = {
    "proposed": "Proposed",
    "approved": "Running",
    "executing": "Running",
    "executed": "Awaiting Measurement",
    "awaiting_verification": "Awaiting Measurement",
    "verified": "Verified",
    "rewarded": "Verified",
    "rejected": "Rejected",
    "failed": "Failed",
}
INCONCLUSIVE_DISPLAY = "Inconclusive"  # status stays `verified`; the outcome row says nothing could be concluded
SUMMARY_GROUPS = {
    "running": ("approved", "executing"),
    "awaiting_measurement": ("executed", "awaiting_verification"),
    "verified": ("verified", "rewarded"),
}


def exp_code(number: int | None, exp_id: Any) -> str:
    return f"EXP-{number:04d}" if number is not None else f"EXP-{str(exp_id)[:8]}"


def scalar_metric(metrics: Any, key: str | None) -> float | None:
    """Pull one comparable number out of a before/after metrics blob without inventing values."""
    if isinstance(metrics, dict):
        if key and isinstance(metrics.get(key), int | float):
            return float(metrics[key])
        inner = metrics.get("metrics")
        if isinstance(inner, list | dict) and inner is not metrics:
            return scalar_metric(inner, key)
        for v in metrics.values():
            if isinstance(v, int | float) and not isinstance(v, bool):
                return float(v)
        return None
    if isinstance(metrics, list):
        for m in metrics:
            if isinstance(m, dict) and (
                key is None or key in (m.get("key"), m.get("label"), m.get("metric"))
            ):
                for f in ("value", "after", "current"):
                    if isinstance(m.get(f), int | float):
                        return float(m[f])
        return None
    return None


def primary_key_of(incident: Incident | None) -> str | None:
    if incident is None:
        return None
    for m in incident.metrics or []:
        if isinstance(m, dict):
            return m.get("key") or m.get("metric") or m.get("label")
    return None


async def preload_experiment_rows(session: AsyncSession, exps: list[Any]) -> dict[str, dict]:
    """One query per related table for a whole page of experiments (the list view used to issue 3 per row)."""
    mod = adapters.interventions_models()
    pm = adapters.policy_models()
    out: dict[str, dict] = {"reward": {}, "execution": {}, "policy": {}, "outcome": {}}
    if not exps or mod is None:
        return out
    ids = [e.id for e in exps]
    iv_ids = list({g(e, "intervention_id") for e in exps if g(e, "intervention_id") is not None})
    if hasattr(mod, "Reward"):
        for r in (await session.execute(select(mod.Reward).where(mod.Reward.experiment_id.in_(ids)))).scalars():
            out["reward"].setdefault(r.experiment_id, r)
    if hasattr(mod, "Execution") and iv_ids:
        rows = (await session.execute(
            select(mod.Execution).where(mod.Execution.intervention_id.in_(iv_ids))
            .order_by(mod.Execution.created_at.desc()))).scalars()
        for r in rows:
            out["execution"].setdefault(r.intervention_id, r)
    if hasattr(mod, "ExperimentOutcome"):
        for r in (await session.execute(
                select(mod.ExperimentOutcome).where(mod.ExperimentOutcome.experiment_id.in_(ids)))).scalars():
            out["outcome"][r.experiment_id] = r
    pv_ids = list({g(e, "policy_version_id") for e in exps if g(e, "policy_version_id") is not None})
    if pm is not None and hasattr(pm, "PolicyVersion") and pv_ids:
        for pv in (await session.execute(select(pm.PolicyVersion).where(pm.PolicyVersion.id.in_(pv_ids)))).scalars():
            out["policy"][pv.id] = g(pv, "version")
    return out


def inconclusive_reason(outcome_row: Any) -> str | None:
    """The persisted reason an outcome was inconclusive (methodology.reason); null when none was recorded."""
    m = g(outcome_row, "methodology") or {}
    r = m.get("reason") if isinstance(m, dict) else None
    return str(r) if r else None


async def experiment_row(
    session: AsyncSession, exp: Any, incident: Incident | None, action_row: Any | None,
    pre: dict[str, dict] | None = None,
) -> ExperimentRow:
    mod = adapters.interventions_models()
    if pre is not None:
        reward = pre["reward"].get(exp.id)
    else:
        reward = await latest(session, getattr(mod, "Reward", None), "experiment_id", exp.id) if mod else None
    key = primary_key_of(incident)
    before = scalar_metric(g(exp, "before_metrics"), key)
    after = scalar_metric(g(exp, "after_metrics"), key)
    status = str(g(exp, "status"))
    dry_run = bool(g(exp, "dry_run", False))
    display = DISPLAY_STATUS.get(status, status)
    mod_exec = getattr(mod, "Execution", None) if mod else None
    if pre is not None:
        execution = pre["execution"].get(g(exp, "intervention_id"))
    else:
        execution = (
            await latest(session, mod_exec, "intervention_id", g(exp, "intervention_id")) if mod_exec else None
        )
    awaiting_human = status == "approved" and execution is not None and (
        g(execution, "status") == "awaiting_human_execution"
    )
    if awaiting_human:
        display = "Awaiting Execution"  # a human still has to apply the package and mark it executed
    outcome_row = None
    if mod is not None and hasattr(mod, "ExperimentOutcome"):
        outcome_row = pre["outcome"].get(exp.id) if pre is not None else await latest(
            session, mod.ExperimentOutcome, "experiment_id", exp.id)
    outcome_label = g(outcome_row, "outcome") if outcome_row is not None else None
    if hasattr(outcome_label, "value"):
        outcome_label = outcome_label.value
    if outcome_label == "inconclusive" and not dry_run:
        display = INCONCLUSIVE_DISPLAY
    if dry_run and status in ("executed", "awaiting_verification", "verified", "rewarded"):
        display = "Never Measured"  # a GitHub dry-run preview changed nothing; it can never be verified or rewarded
    return ExperimentRow(
        id=exp.id,
        number=g(exp, "number"),
        code=exp_code(g(exp, "number"), exp.id),
        incident_id=g(exp, "incident_id"),
        incident_number=incident.number if incident else None,
        incident_title=incident.title if incident else None,
        action=str(g(action_row, "action")) if action_row is not None else None,
        action_title=g(action_row, "title") if action_row is not None else None,
        started_at=g(exp, "executed_at") or g(exp, "created_at"),
        status=status,
        display_status=display,
        dry_run=dry_run,
        executor=g(exp, "executor") or (g(execution, "executor") if execution else None) or "manual",
        awaiting_human_execution=awaiting_human,
        deviation=bool(g(execution, "deviation", False)) if execution else False,
        measured=bool(g(exp, "after_metrics")) and not dry_run,
        before=before,
        after=after,
        before_after_label=incident.metrics[0].get("label")
        if incident and incident.metrics and isinstance(incident.metrics[0], dict)
        else None,
        reward=g(reward, "total") if reward else None,
        outcome=outcome_label,
        inconclusive_reason=inconclusive_reason(outcome_row) if outcome_label == "inconclusive" else None,
        policy_version=pre["policy"].get(g(exp, "policy_version_id")) if pre is not None
        else await policy_version_label(session, g(exp, "policy_version_id")),
    )


async def experiment_timeline(
    session: AsyncSession, exp: Any, intervention_id: Any, incident_id: Any
) -> list[TimelineEntry]:
    ids = {str(x) for x in (exp.id, intervention_id, incident_id) if x is not None}
    rows = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.entity_id.in_(ids)).order_by(AuditEvent.at)
            )
        )
        .scalars()
        .all()
    )
    entries = [
        TimelineEntry(
            at=r.at, event=f"{r.entity_type}.{r.event}", actor=r.actor, detail=jsonable(r.metadata_ or {})
        )
        for r in rows
    ]
    for field, label in (
        ("created_at", "experiment.created"),
        ("executed_at", "experiment.executed"),
        ("evaluated_at", "experiment.evaluated"),
    ):
        ts = g(exp, field)
        if ts is not None:
            entries.append(TimelineEntry(at=ts, event=label, actor="system"))
    entries.sort(key=lambda e: e.at or datetime.min.replace(tzinfo=UTC))
    return entries
