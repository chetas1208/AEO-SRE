import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import ActorDep, PageDep, SessionDep
from app.api.errors import Conflict, NotFound
from app.api.locks import entity_lock
from app.api.mappers import (
    SUMMARY_GROUPS,
    exp_code,
    experiment_row,
    experiment_timeline,
    inconclusive_reason,
    jsonable,
    latest,
    preload_experiment_rows,
    row_dict,
)
from app.core.audit import audit
from app.core.queue import enqueue
from app.domain.enums import ExperimentStatus
from app.domain.errors import ExperimentNotVerifiable
from app.experiments import window as vwindow
from app.models.core import Incident, Job
from app.models.interventions import Approval, Execution, Experiment, ExperimentOutcome, Intervention, Reward
from app.models.policy import PolicyVersion
from app.schemas.experiments import (
    DeclaredMetrics,
    ExperimentDetail,
    ExperimentList,
    ExperimentRow,
    ExperimentSpec,
    ExperimentSummary,
    OutcomeOut,
    OverrideOut,
    VerificationInfo,
    VerifyOut,
)
from app.services import incidents as svc

log = structlog.get_logger()
router = APIRouter(prefix="/api/experiments", tags=["experiments"])
VERIFIABLE = {ExperimentStatus.EXECUTED.value, ExperimentStatus.AWAITING_VERIFICATION.value}


def _status_values(group_or_status: str) -> list[str]:
    return list(SUMMARY_GROUPS.get(group_or_status, (group_or_status,)))


@router.get("", response_model=ExperimentList)
async def list_experiments(
    session: SessionDep,
    page: PageDep,
    status: Annotated[
        str | None, Query(description="raw status, or running|awaiting_measurement|verified")
    ] = None,
    incident_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    action: str | None = None,
):
    base = (
        select(Experiment, Incident, Intervention)
        .join(Incident, Incident.id == Experiment.incident_id)
        .join(Intervention, Intervention.id == Experiment.intervention_id, isouter=True)
    )
    if status:
        base = base.where(Experiment.status.in_(_status_values(status)))
    if org_id:
        base = base.where(Incident.org_id == org_id)
    if incident_id:
        base = base.where(Experiment.incident_id == incident_id)
    if action:
        base = base.where(Experiment.selected_action == action)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(Experiment.created_at.desc()).limit(page.limit).offset(page.offset)
        )
    ).all()
    pre = await preload_experiment_rows(session, [e for e, _, _ in rows])
    items: list[ExperimentRow] = [await experiment_row(session, e, inc, iv, pre) for e, inc, iv in rows]

    cq = select(Experiment.status, func.count()).where(Experiment.dry_run.is_(False)).group_by(Experiment.status)
    if org_id:
        cq = cq.join(Incident, Incident.id == Experiment.incident_id).where(Incident.org_id == org_id)
    counts = dict((await session.execute(cq)).all())  # dry runs are never measured: excluded from the groups
    by_value = {getattr(k, "value", k): v for k, v in counts.items()}
    summary = ExperimentSummary(
        **{g: sum(by_value.get(s, 0) for s in members) for g, members in SUMMARY_GROUPS.items()},
        total=sum(by_value.values()),
    )
    return ExperimentList(items=items, total=total, limit=page.limit, offset=page.offset, summary=summary)


async def _get(session, ident: str) -> Experiment:
    exp = None
    try:
        exp = await session.get(Experiment, uuid.UUID(ident))
    except ValueError:
        digits = ident.upper().removeprefix("EXP-").lstrip("0") or "0"
        if digits.isdigit():
            exp = (await session.execute(select(Experiment).where(Experiment.number == int(digits)))).scalar()
    if exp is None:
        raise NotFound(f"experiment {ident} not found")
    return exp


@router.get("/{experiment_id}", response_model=ExperimentDetail)
async def get_experiment(experiment_id: str, session: SessionDep):
    exp = await _get(session, experiment_id)
    inc = await session.get(Incident, exp.incident_id)
    iv = await session.get(Intervention, exp.intervention_id)
    approval = await session.get(Approval, exp.approval_id) if exp.approval_id else None
    if approval is None:
        approval = await latest(session, Approval, "intervention_id", exp.intervention_id)
    execution = await session.get(Execution, exp.execution_id) if exp.execution_id else None
    if execution is None:
        execution = await latest(session, Execution, "intervention_id", exp.intervention_id)
    reward = (await session.execute(select(Reward).where(Reward.experiment_id == exp.id))).scalar()
    # an INCONCLUSIVE outcome has no reward row but is final: it is not "awaiting reward"
    assessed = (await session.execute(select(ExperimentOutcome.id).where(
        ExperimentOutcome.experiment_id == exp.id))).first() is not None
    pv = await session.get(PolicyVersion, exp.policy_version_id) if exp.policy_version_id else None
    outcome_row = (await session.execute(select(ExperimentOutcome).where(
        ExperimentOutcome.experiment_id == exp.id))).scalar()
    row = await experiment_row(session, exp, inc, iv, {  # reuse the list mapper's batch inputs for this one row
        "reward": {exp.id: reward} if reward else {}, "execution": {exp.intervention_id: execution} if execution else {},
        "policy": {exp.policy_version_id: pv.version} if pv else {},
        "outcome": {exp.id: outcome_row} if outcome_row else {}})
    status = exp.status.value if hasattr(exp.status, "value") else str(exp.status)
    return ExperimentDetail(
        id=exp.id,
        code=exp_code(exp.number, exp.id),
        summary={
            "number": exp.number,
            "status": status,
            "display_status": row.display_status,
            "incident": {"id": str(inc.id), "number": inc.number, "title": inc.title} if inc else None,
            "action": row.action,
            "action_title": row.action_title,
            "created_at": jsonable(exp.created_at),
            "executed_at": jsonable(exp.executed_at),
            "evaluated_at": jsonable(exp.evaluated_at),
            "verification_window_start": jsonable(exp.verification_window_start),
            "verification_window_end": jsonable(exp.verification_window_end),
            "dry_run": exp.dry_run,
        },
        why_selected={
            "reason": exp.reason,
            "selection_basis": jsonable(exp.selection_basis),
            "cold_start": exp.cold_start,
            "policy_probability": exp.policy_probability,
            "policy_version": row.policy_version,
            "alternatives": jsonable(exp.alternatives),
            "rationale": iv.rationale if iv else None,
            "risk": jsonable(iv.risk) if iv else None,
        },
        context_at_decision={"context_vector": jsonable(exp.context_vector)},
        evidence_snapshot=jsonable(exp.evidence_snapshot),
        action_executed={
            "proposed_change": jsonable(exp.proposed_change),
            "approved_change": jsonable(exp.approved_change),
            "executor": exp.executor,
            "reference": exp.execution_reference,
            "dry_run": exp.dry_run,
            "execution": row_dict(execution) if execution else None,
        },
        approval=row_dict(approval) if approval else None,
        before_metrics=jsonable(exp.before_metrics),
        after_metrics=jsonable(exp.after_metrics),
        reward=row_dict(reward) if reward else None,
        policy={k: v for k, v in row_dict(pv).items() if k not in ("state", "priors")} if pv else None,
        timeline=await experiment_timeline(session, exp, exp.intervention_id, exp.incident_id),
        display_status=row.display_status,
        spec=_spec_out(exp.spec),
        declared_metrics=_declared_metrics(exp.spec),
        outcome=_outcome_out(outcome_row),
        override=_override_out(exp),
        verification=_verification_out(exp),
        awaiting_reward=reward is None and not assessed and status not in ("rejected", "failed"),
        protection=await _protection(session, exp),
    )


async def _protection(session, exp):
    from app.changeguard.service import protection_for

    return await protection_for(session, exp)


def _spec_out(spec) -> ExperimentSpec | None:
    if not isinstance(spec, dict) or not spec:
        return None
    return ExperimentSpec(**{k: spec.get(k) for k in ExperimentSpec.model_fields})


def _declared_metrics(spec) -> DeclaredMetrics | None:
    if not isinstance(spec, dict) or not spec.get("primary_metric"):
        return None
    return DeclaredMetrics(primary=spec.get("primary_metric"), secondary=list(spec.get("secondary_metrics") or []))


def _outcome_out(o) -> OutcomeOut | None:
    if o is None:
        return None
    label = o.outcome.value if hasattr(o.outcome, "value") else str(o.outcome)
    meth = o.methodology if isinstance(o.methodology, dict) else {}
    causal = meth.get("causal") if isinstance(meth.get("causal"), dict) else {}
    return OutcomeOut(
        label=label, observe_outcome=o.observe_outcome, reward_total=o.reward_total,
        components={k: float(v) for k, v in (o.components or {}).items() if isinstance(v, int | float)},
        confounders=[c if isinstance(c, dict) else {"kind": str(c)} for c in (o.confounders or [])],
        causal_confidence=o.causal_confidence, causal_statement=causal.get("statement"),
        learning_applied=bool(o.learning_applied),
        inconclusive_reason=inconclusive_reason(o) if label == "inconclusive" else None,
        observed_at=o.observed_at, evaluated_at=o.evaluated_at, methodology=jsonable(meth))


def _override_out(exp) -> OverrideOut:
    executed = exp.selected_action.value if hasattr(exp.selected_action, "value") else exp.selected_action
    policy_action = exp.policy_action
    overridden = bool(exp.override_reason or exp.override_by) or (
        policy_action is not None and executed is not None and policy_action != executed)
    return OverrideOut(overridden=overridden, policy_action=policy_action, executed_action=executed,
                       reason=exp.override_reason, by=exp.override_by)


VERIFICATION_RULES = [
    "A verification attempt is allowed once the server clock reaches eligible_at (inclusive).",
    "A measurement counts only if its source observed_at is at or after eligible_at and after execution.",
    "A measurement after window_end is still accepted and flagged late.",
    "Dry-run executions are never verified.",
]


def _verification_out(exp) -> VerificationInfo | None:
    start = exp.verification_window_start
    if start is None and exp.executed_at is None:
        return None
    spec = exp.spec if isinstance(exp.spec, dict) else {}
    delay = spec.get("delay_hours")
    if delay is None and start is not None and exp.executed_at is not None:
        delay = (vwindow.aware(start) - vwindow.aware(exp.executed_at)).total_seconds() / 3600
    return VerificationInfo(
        executed_at=exp.executed_at, eligible_at=start, window_end=exp.verification_window_end, delay_hours=delay,
        is_open=(vwindow.now() >= vwindow.aware(start)) if start is not None else None, rules=VERIFICATION_RULES)


async def lock_experiment(experiment_id: str):
    async with entity_lock("experiment", experiment_id):
        yield


@router.post(
    "/{experiment_id}/verify", response_model=VerifyOut, status_code=202, dependencies=[Depends(lock_experiment)]
)
async def verify(experiment_id: str, session: SessionDep, actor: ActorDep, force: bool = False):
    """Queue verification. Reward is computed only if a qualifying post-intervention observation exists."""
    exp = await _get(session, experiment_id)
    status = exp.status.value if hasattr(exp.status, "value") else str(exp.status)
    if status not in VERIFIABLE:
        raise Conflict(
            f"experiment is {status}; only executed experiments can be verified", {"status": status}
        )
    if exp.dry_run:
        raise Conflict("dry-run executions changed nothing and cannot be verified", {"dry_run": True})
    exp_id = exp.id
    start = exp.verification_window_start
    if start is None:
        raise ExperimentNotVerifiable("experiment has no verification window yet", {"status": status})
    gate = vwindow.attempt_allowed(start, dry_run=bool(exp.dry_run))  # the ONE window service; `force` never relaxes it
    if not gate.ok:
        raise ExperimentNotVerifiable.too_early(vwindow.aware(start))
    active = (
        await session.execute(
            select(Job).where(Job.kind == "verify_experiment", Job.status.in_(("queued", "running")))
            .order_by(Job.created_at.desc()).limit(50)
        )
    ).scalars().all()
    pending = next((j for j in active if (j.payload or {}).get("experiment_id") == str(exp_id)), None)
    if pending is not None:  # retry-safe: a repeated request returns the verification already in flight
        return VerifyOut(experiment_id=exp_id, job=svc.job_ref(pending))
    await audit(
        session,
        "human",
        actor,
        "experiment",
        exp_id,
        "experiment.verify_requested",
        {"force": force},
        commit=True,
    )
    job_id = await enqueue("verify_experiment", {"experiment_id": str(exp_id), "force": force})
    job = await session.get(Job, job_id)
    await session.refresh(job)
    return VerifyOut(experiment_id=exp_id, job=svc.job_ref(job))
