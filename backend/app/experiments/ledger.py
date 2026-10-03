"""Experiment ledger: every intervention that is proposed becomes a durable, fully-recorded experiment."""
from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionType, ApprovalStatus, ExperimentStatus, SelectionBasis
from app.experiments.metrics import jsonable, normalize_metrics
from app.experiments.status import transition
from app.experiments.window import DEFAULT_WINDOW, VerificationWindow
from app.models.interventions import Approval, Execution, Experiment, Intervention
from app.services.approvals import GRANTED, effective_change, require_executable_approval

# The window definition lives in ONE place (app.experiments.window); re-exported here for existing imports.


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        v = obj.get(n) if isinstance(obj, Mapping) else getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _alternatives(decision: Any, selected: ActionType) -> list[dict]:
    scores = _get(decision, "scores", default=[]) or []
    out = []
    for s in scores:
        d = s.to_json() if hasattr(s, "to_json") else dict(s)
        if ActionType(d["action"]) != selected:
            out.append(jsonable(d))
    return out


async def _resolve_policy_version_id(session: AsyncSession, intervention: Intervention, decision: Any):
    if intervention.policy_version_id:
        return intervention.policy_version_id
    pv = _get(decision, "policy_version_id")
    if pv:
        return pv
    version = _get(decision, "policy_version")
    if isinstance(version, str):
        from app.core.db import Base

        table = Base.metadata.tables.get("policy_versions")
        if table is not None:
            return (await session.execute(select(table.c.id).where(table.c.version == version))).scalar()
    return None


async def open_experiment(
    session: AsyncSession,
    intervention: Intervention,
    decision: Any,
    incident: Any,
    before_metrics: Any,
    evidence_snapshot: Any,
    context_vector: Any,
    *,
    alternatives: Sequence[Mapping[str, Any]] | None = None,
    reason: str | None = None,
    window: VerificationWindow = DEFAULT_WINDOW,
    now: datetime | None = None,
    override_reason: str | None = None,
    override_by: str | None = None,
) -> Experiment:
    """Record the full before-state and decision for an intervention. Status = proposed.

    Also declares (before activation, immutable afterwards) the hypothesis + primary/secondary metrics
    (`Experiment.spec`, see `app.experiments.spec`), the collision `target_key`, and the policy decision link. If the
    executed action differs from the policy's pick, the override is recorded (policy action vs executed action +
    reason + who) and the outcome belongs to the EXECUTED action only.

    `decision` is a policy `Decision`, a `PolicyDecision` row, or a dict (duck-typed): it supplies
    policy version, propensity (`probability`), scores (-> alternatives) and selection basis.
    """
    if intervention.incident_id != incident.id:
        raise ValueError("intervention does not belong to the given incident")
    before = normalize_metrics(before_metrics)
    if not before:
        raise ValueError("before_metrics must contain at least one measured numeric metric")
    now = now or datetime.now(UTC)
    action = ActionType(intervention.action)

    probability = _get(decision, "probability", "policy_probability")
    decided_action = _get(decision, "action", "selected_action")
    basis = intervention.selection_basis or _get(decision, "selection_basis")
    note = reason
    if decided_action is not None and ActionType(decided_action) != action:
        # human/other override: the policy's propensity belongs to a different action, so do not attach it.
        probability = None
        basis = SelectionBasis.MANUAL_OVERRIDE
        note = (note + " " if note else "") + f"policy chose {ActionType(decided_action).value}; override."

    w_start, w_end = window.bounds(now)
    from app.experiments.collision import target_key_for
    from app.experiments.spec import build_spec
    from app.models.evidence import Hypothesis

    hyp = await session.get(Hypothesis, intervention.hypothesis_id) if intervention.hypothesis_id else None
    spec = build_spec(
        action=action, root_cause=hyp.title if hyp else None, category=getattr(incident, "category", None),
        before_metrics=before, window_start=w_start, window_end=w_end, executed_after=window.delay,
        declared_at=now).to_json()
    overridden = decided_action is not None and ActionType(decided_action) != action
    exp = Experiment(
        id=uuid.uuid4(),
        incident_id=incident.id,
        intervention_id=intervention.id,
        policy_version_id=await _resolve_policy_version_id(session, intervention, decision),
        policy_probability=float(probability) if probability is not None else None,
        context_vector=jsonable(context_vector if context_vector is not None else []),
        alternatives=jsonable(list(alternatives)) if alternatives is not None else _alternatives(decision, action),
        evidence_snapshot=jsonable(evidence_snapshot if evidence_snapshot is not None else {}),
        before_metrics=before,
        after_metrics=None,
        status=ExperimentStatus.PROPOSED,
        verification_window_start=w_start,
        verification_window_end=w_end,
        selected_action=action,
        selection_basis=SelectionBasis(basis) if basis else None,
        cold_start=_get(decision, "cold_start"),
        reason=note or intervention.rationale or "",
        proposed_change=jsonable(intervention.proposed_change or {}),
        timeline=[{"from": None, "to": ExperimentStatus.PROPOSED.value, "actor": "system",
                   "reason": "experiment opened", "at": now.isoformat()}],
        spec=spec,
        target_key=target_key_for(intervention, incident),
        policy_decision_id=_get(decision, "policy_decision_id") or (
            _get(decision, "id") if hasattr(decision, "selected_action") else None),
        policy_action=ActionType(decided_action).value if decided_action is not None else None,
        override_reason=(override_reason or note) if overridden else None,
        override_by=override_by if overridden else None,
    )
    session.add(exp)
    await session.flush()
    return exp


async def activation_check(session: AsyncSession, experiment: Experiment, *, require_spec: bool = True) -> None:
    """Gate before an experiment may be activated (package issued / execution started): the hypothesis and metrics
    must be declared and complete, and no other active intervention may share its target or prompt-cluster scope
    (contamination). Raises SpecError / ExperimentCollision; changes nothing."""
    from app.experiments.collision import assert_no_collision
    from app.experiments.spec import validate_spec

    if require_spec:
        validate_spec(experiment.spec, before_metrics=experiment.before_metrics)
    await assert_no_collision(session, experiment)


async def attach_approval(
    session: AsyncSession, experiment: Experiment, approval: Approval | None = None, *, now: datetime | None = None
) -> Experiment:
    """Move proposed -> approved / rejected according to a DECIDED approval. `observe` needs no approval."""
    action = ActionType(experiment.selected_action)
    if approval is None:
        if action != ActionType.OBSERVE:
            raise ValueError("an approval is required")
        from app.experiments.spec import validate_spec

        validate_spec(experiment.spec, before_metrics=experiment.before_metrics)
        return transition(experiment, ExperimentStatus.APPROVED, "system", "observe needs no approval", now=now)
    if approval.intervention_id != experiment.intervention_id:
        raise ValueError("approval belongs to a different intervention")
    status = ApprovalStatus(approval.status)
    if status == ApprovalStatus.PENDING:
        raise ValueError("approval is still pending")
    experiment.approval_id = approval.id
    if status in GRANTED:
        if approval.decided_actor_type != "human":
            raise ValueError("approval was not decided by a human")
        from app.experiments.spec import validate_spec

        validate_spec(experiment.spec, before_metrics=experiment.before_metrics)  # declared BEFORE activation
        intervention = await session.get(Intervention, experiment.intervention_id)
        experiment.approver = approval.decided_by
        experiment.approved_change = effective_change(approval, intervention)
        transition(experiment, ExperimentStatus.APPROVED, approval.decided_by or "human",
                   f"approval {status.value}", now=now)
    else:  # rejected / expired
        experiment.approver = approval.decided_by
        transition(experiment, ExperimentStatus.REJECTED, approval.decided_by or "system",
                   f"approval {status.value}", now=now)
    await session.flush()
    return experiment


async def begin_execution(
    session: AsyncSession, experiment: Experiment, execution: Execution | None = None, *,
    now: datetime | None = None,
) -> Experiment:
    """approved -> executing. Re-checks the live approval record, not just the experiment copy."""
    intervention = await session.get(Intervention, experiment.intervention_id)
    approval = await require_executable_approval(session, intervention)
    if approval is not None and experiment.approval_id != approval.id:
        raise ValueError("experiment is not linked to the granting approval")
    await activation_check(session, experiment, require_spec=False)  # the spec was already required at approval
    if execution is not None:
        experiment.execution_id = execution.id
        experiment.executor = execution.executor
    transition(experiment, ExperimentStatus.EXECUTING, experiment.approver or "system", "execution started",
               now=now)
    await session.flush()
    return experiment


async def mark_executed(
    session: AsyncSession,
    experiment: Experiment,
    execution: Execution | None = None,
    *,
    executed_at: datetime | None = None,
    window: VerificationWindow = DEFAULT_WINDOW,
) -> Experiment:
    """executing -> executed -> awaiting_verification (window anchored at execution time).

    A dry-run execution mutated nothing: the experiment stays `executed`, is flagged `dry_run`, has no
    verification window, and can never be verified or rewarded.
    """
    executed_at = executed_at or (execution.finished_at if execution and execution.finished_at else None) \
        or datetime.now(UTC)
    if execution is not None:
        experiment.execution_id = execution.id
        experiment.executor = execution.executor
        experiment.execution_reference = execution.reference
        experiment.dry_run = bool(execution.dry_run)
    experiment.executed_at = executed_at
    transition(experiment, ExperimentStatus.EXECUTED, experiment.executor or "system", "executed",
               now=executed_at)
    await session.flush()  # the ORM guard validates each status change against the persisted status
    if experiment.dry_run:
        experiment.verification_window_start = experiment.verification_window_end = None
    else:
        experiment.verification_window_start, experiment.verification_window_end = window.bounds(executed_at)
        transition(experiment, ExperimentStatus.AWAITING_VERIFICATION, "system",
                   "waiting for post-intervention observation", now=executed_at)
    await session.flush()
    return experiment


async def fail_experiment(
    session: AsyncSession, experiment: Experiment, reason: str, actor: str = "system", *,
    now: datetime | None = None,
) -> Experiment:
    transition(experiment, ExperimentStatus.FAILED, actor, reason, now=now)
    await session.flush()
    return experiment


async def get_experiment(session: AsyncSession, experiment_id: uuid.UUID) -> Experiment | None:
    return await session.get(Experiment, experiment_id)
