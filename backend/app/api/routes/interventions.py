import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import ActorDep, BusDep, SessionDep
from app.api.errors import ApiError, Conflict, IllegalTransitionError, NotFound
from app.api.locks import entity_lock
from app.api.mappers import intervention_out
from app.changeguard import service as change_guard
from app.core.audit import audit
from app.core.queue import QueueUnavailable, enqueue
from app.domain.enums import ApprovalStatus, IncidentState, StepStatus
from app.domain.errors import ApprovalDigestMismatch, ChangeGuardBlocked
from app.experiments import ledger
from app.incidents import state_machine
from app.incidents.state_machine import IllegalTransition
from app.interventions import manual as manual_exec
from app.interventions.executor import ExecutionRefused, normalize_executor_choice, select_executor
from app.learning.review import UnknownRejectionReason, normalize_reason
from app.models.core import Incident, Job
from app.models.interventions import Experiment, Intervention
from app.schemas.changeguard import ChangeGuardVerdict
from app.schemas.interventions import (
    ApprovalRequest,
    ExecutedOut,
    ExecuteRequest,
    InterventionActionOut,
    InterventionOut,
    ModifyRequest,
    RecordExecutionRequest,
)
from app.services import approvals as appr
from app.services import incidents as svc

log = structlog.get_logger()
router = APIRouter(prefix="/api/interventions", tags=["interventions"])
S = IncidentState
DECIDABLE = (S.INTERVENTION_PROPOSED, S.AWAITING_APPROVAL)


async def _load(session, intervention_id: uuid.UUID) -> tuple[Intervention, Incident]:
    iv = await session.get(Intervention, intervention_id)
    if iv is None:
        raise NotFound(f"intervention {intervention_id} not found")
    return iv, await svc.get_incident(session, iv.incident_id)


def _step(
    inc: Incident, dst: S, actor: str, reason: str, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    src = inc.state
    try:
        return state_machine.transition(inc, dst, actor, reason, metadata=meta)
    except IllegalTransition as exc:
        raise IllegalTransitionError(str(exc), {"from": str(src), "to": dst.value}) from exc


def _approval_error(exc: appr.ApprovalError) -> ApiError:
    if isinstance(exc, appr.HumanActorRequired):
        return ApiError(str(exc), status_code=403, error_type="forbidden")
    return Conflict(str(exc), {"reason": type(exc).__name__})


async def _experiment_for(session, intervention_id: uuid.UUID) -> Experiment | None:
    stmt = select(Experiment).where(Experiment.intervention_id == intervention_id)
    return (await session.execute(stmt.order_by(Experiment.created_at.desc()).limit(1))).scalars().first()


def _check_executor(action: Any, choice: str | None, dry_run: bool | None = None) -> str:
    """Validate an explicit executor choice BEFORE anything is decided. Default (None) is manual."""
    try:
        which = normalize_executor_choice(choice)
        select_executor(action, choice=which, dry_run=dry_run)
    except ExecutionRefused as exc:
        raise Conflict(str(exc), {"executor": choice, "code": exc.code}) from exc
    return which


async def _activate_manual(session, intervention_id: uuid.UUID) -> str | None:
    """Approval activates the experiment: issue the manual package (observe starts observing). Never fails the
    approval itself: returns a message when activation could not complete."""
    from app.services import pipeline

    try:
        await pipeline.execute(session, intervention_id)
    except ApprovalDigestMismatch:
        await session.rollback()
        raise  # the change was edited after approval: a hard 409, not a soft "could not be activated yet"
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.warning("approval.activation_failed", intervention_id=str(intervention_id), error=repr(exc))
        return f"approved, but the experiment could not be activated yet: {exc}"
    return None


async def _repeat_decision(session, iv: Intervention, inc: Incident, status: ApprovalStatus):
    if status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        return None
    latest = await appr.latest_approval(session, iv.id)
    if latest is None or latest.status != status or latest.decided_actor_type != "human":
        return None
    iv_id, inc_id = iv.id, inc.id
    message = None
    if status == ApprovalStatus.APPROVED and S(inc.state) == S.APPROVED:
        message = await _activate_manual(session, iv_id)  # re-entrant: re-returns the issued package
    session.expire_all()
    iv = await session.get(Intervention, iv_id)
    inc = await svc.get_incident(session, inc_id)
    exp = await _experiment_for(session, iv_id)
    return InterventionActionOut(
        intervention=await intervention_out(session, iv), incident_state=str(inc.state),
        experiment_id=exp.id if exp else None, job=None, message=message,
    )


async def _refuse_if_already_decided_otherwise(session, iv: Intervention, status: ApprovalStatus) -> None:
    """A rejected intervention is never approved by retrying (and an approved one never rejected): the recorded
    decision stands. Expired approvals may be re-requested."""
    latest = await appr.latest_approval(session, iv.id)
    if latest is not None and latest.status in (*appr.GRANTED, ApprovalStatus.REJECTED) and latest.status != status:
        raise IllegalTransitionError(
            f"intervention was already {ApprovalStatus(latest.status).value}; a recorded decision is final",
            {"decision": ApprovalStatus(latest.status).value, "requested": status.value},
        )


async def _require_eligible_action(session, iv: Intervention) -> None:
    """An action the operator has masked out in policy settings can never be activated, even from a stale proposal."""
    from app.domain.enums import ActionType
    from app.domain.errors import ActionNotEligible
    from app.policy.store import PolicyStore

    action = ActionType(iv.action)
    if action == ActionType.OBSERVE:
        return
    mask = await PolicyStore(session).load_allowed_actions() or {}
    if mask.get(action.value, True) is False:
        raise ActionNotEligible(
            f"{action.value} is disabled in policy settings", {"action": action.value, "allowed": False}
        )


async def _decide(
    session,
    bus,
    intervention_id: uuid.UUID,
    actor: str,
    status: ApprovalStatus,
    note: str | None,
    modified_change: dict | None = None,
    executor: str | None = None,
    reason_code: str | None = None,
    review_reason: str | None = None,
) -> InterventionActionOut:
    iv, inc = await _load(session, intervention_id)
    repeat = await _repeat_decision(session, iv, inc, status)
    if repeat is not None:  # retry-safe: the same decision again returns the recorded outcome, no new effects
        return repeat
    prior = (
        await session.execute(
            select(Intervention.action).where(
                Intervention.incident_id == inc.id, Intervention.selected.is_(True), Intervention.id != iv.id
            )
        )
    ).scalar()
    executed_action = str(iv.action.value if hasattr(iv.action, "value") else iv.action)
    recommended_action = (
        str(prior.value if hasattr(prior, "value") else prior) if prior is not None else executed_action
    )
    state = S(inc.state)
    which = _check_executor(iv.action, executor) if status in appr.GRANTED else "manual"
    await _refuse_if_already_decided_otherwise(session, iv, status)
    if status in appr.GRANTED:
        await _require_eligible_action(session, iv)
    if state not in DECIDABLE:
        raise IllegalTransitionError(
            f"cannot {status.value} an intervention while the incident is {state.value}",
            {"state": state.value, "allowed_states": [s.value for s in DECIDABLE]},
        )
    granted = status in appr.GRANTED
    guard = None
    if granted:
        guard = await _guard_at_approval(session, iv, status, modified_change, actor, review_reason)
    try:
        approval = await appr.request_approval(session, iv, requested_by="system")
        if state == S.INTERVENTION_PROPOSED:
            _step(inc, S.AWAITING_APPROVAL, "system", "approval requested", {"intervention_id": str(iv.id)})
        # X-Actor is trusted for the hackathon (production needs real auth), but a machine-looking actor name
        # must never be able to approve/reject/modify.
        actor_type = "system" if actor.lower() in manual_exec.NON_HUMAN_ACTORS else "human"
        # the approval binds to the digest of exactly this change + experiment context
        await appr.decide_approval(
            session, approval, status, actor, note, modified_change, actor_type=actor_type,
            action_digest=guard.check.action_digest if guard is not None else None,
        )
    except appr.ApprovalError as exc:
        await session.rollback()
        raise _approval_error(exc) from exc
    if granted:
        _step(inc, S.APPROVED, actor, f"intervention {status.value}", {"intervention_id": str(iv.id)})
        iv.selected = True
    else:  # rejected: back to intervention_proposed so another candidate can be chosen
        _step(inc, S.INTERVENTION_PROPOSED, actor, "intervention rejected", {"intervention_id": str(iv.id)})
    exp = await _experiment_for(session, iv.id)
    message = None
    if exp is not None:
        try:
            await ledger.attach_approval(session, exp, approval)
        except Exception as exc:
            # The decision and the ledger link are ONE transaction: if the experiment cannot take the approval
            # (spec invalid, collision, illegal transition...) nothing is recorded at all.
            await session.rollback()
            if type(exc) is ValueError:  # plain ValueError = the ledger refused this approval
                raise Conflict(str(exc), {"experiment_id": str(exp.id)}) from exc
            raise
    else:
        message = "approval recorded; no experiment is open for this intervention yet"
    await audit(
        session, "human", actor, "intervention", iv.id, f"intervention.{status.value}",
        {
            "incident_id": str(inc.id), "note": note, "approval_id": str(approval.id),
            "reason_code": reason_code, "recommended_action": recommended_action,
            "executed_action": executed_action, "human_reason": note,
            "feeds_reward": False,
            **({"change_guard": {"check_id": str(guard.check.id), "decision": guard.check.decision,
                                 "digest": guard.check.action_digest, "review_reason": review_reason}}
               if guard is not None else {}),
        },
    )  # fmt: skip
    inc_id, iv_id = inc.id, iv.id
    await session.commit()
    await bus.emit(
        None, inc_id, f"approval_{status.value}", StepStatus.SUCCESS,
        f"Intervention {status.value} by {actor}", {"intervention_id": str(iv_id), "actor": actor},
    )  # fmt: skip
    job = None
    if granted and which == "manual":
        activation_error = await _activate_manual(session, iv_id)
        message = activation_error or message
    elif granted:  # explicit optional executor (e.g. GitHub): queue it
        payload = {"intervention_id": str(iv_id), "requested_by": actor, "executor": which}
        try:
            job = await enqueue("execute_intervention", payload)
        except QueueUnavailable as exc:
            message = f"approved, but the {which} execution could not be queued: {exc}"
    session.expire_all()
    iv = await session.get(Intervention, iv_id)
    inc = await svc.get_incident(session, inc_id)
    return InterventionActionOut(
        intervention=await intervention_out(session, iv),
        incident_state=str(inc.state),
        experiment_id=exp.id if exp else None,
        job=svc.job_ref(await session.get(Job, job)) if job else None,
        message=message,
    )


async def _guard_at_approval(session, iv: Intervention, status: ApprovalStatus, modified_change: dict | None,
                             actor: str, review_reason: str | None):
    """Run Change Guard on exactly what is being approved. BLOCK/DELAY can never be approved (no override);
    REQUIRE_REVIEW needs a typed `review_reason`; MERGE/ALLOW pass. OBSERVE is checked and recorded but never
    refused (it changes nothing, and its own experiment is excluded by the guard)."""
    from app.domain.enums import ActionType

    change = modified_change if status == ApprovalStatus.MODIFIED and modified_change else None
    guard = await change_guard.evaluate_intervention(session, iv, change=change)
    decision = guard.check.decision
    observe = ActionType(iv.action) == ActionType.OBSERVE
    refuse = None
    if not observe and change_guard.blocks_approval(decision):
        refuse = f"Change Guard {decision}: this change cannot be approved now"
    elif not observe and decision == "REQUIRE_REVIEW" and len((review_reason or "").strip()) < 3:
        refuse = "Change Guard REQUIRE_REVIEW: a review_reason is required to approve"
    if refuse is not None:
        await audit(session, "human", actor, "intervention", iv.id, "intervention.approval_refused",
                    {"check_id": str(guard.check.id), "decision": decision, "reason": refuse}, commit=True)
        raise ChangeGuardBlocked(refuse, {
            "decision": decision, "check_id": str(guard.check.id), "findings": guard.check.findings,
            "eligible_after": change_guard.iso(guard.check.eligible_after),
            "requires_review_reason": decision == "REQUIRE_REVIEW", "digest": guard.check.action_digest})
    return guard


async def lock_intervention(intervention_id: uuid.UUID):
    """Serialize every mutation of one intervention (approve/reject/modify/execute/executed) across requests."""
    async with entity_lock("intervention", intervention_id):
        yield


LOCKED = [Depends(lock_intervention)]


@router.get("/{intervention_id}", response_model=InterventionOut)
async def get_intervention(intervention_id: uuid.UUID, session: SessionDep):
    iv, _ = await _load(session, intervention_id)
    return await intervention_out(session, iv)


@router.post("/{intervention_id}/change-check", dependencies=LOCKED, response_model=ChangeGuardVerdict)
async def refresh_change_check(intervention_id: uuid.UUID, session: SessionDep):
    """UI/internal: (re)run Change Guard on this proposal and return the fresh verdict. No token (never exposed to
    agents; the token only protects POST /api/change-checks)."""
    iv, _ = await _load(session, intervention_id)
    await change_guard.evaluate_intervention(session, iv)
    await session.commit()
    return await change_guard.verdict_for_intervention(session, iv)


@router.post("/{intervention_id}/approve", dependencies=LOCKED, response_model=InterventionActionOut)
async def approve(
    intervention_id: uuid.UUID,
    session: SessionDep,
    bus: BusDep,
    actor: ActorDep,
    body: ApprovalRequest | None = None,
):
    return await _decide(
        session, bus, intervention_id, actor, ApprovalStatus.APPROVED, body.note if body else None,
        executor=body.executor if body else None, review_reason=body.review_reason if body else None,
    )


@router.post("/{intervention_id}/reject", dependencies=LOCKED, response_model=InterventionActionOut)
async def reject(
    intervention_id: uuid.UUID,
    session: SessionDep,
    bus: BusDep,
    actor: ActorDep,
    body: ApprovalRequest | None = None,
):
    try:
        code = normalize_reason(body.reason_code if body else None)
    except UnknownRejectionReason as exc:
        raise ApiError(f"unknown rejection reason: {exc}", status_code=422, error_type="validation_error") from exc
    return await _decide(
        session, bus, intervention_id, actor, ApprovalStatus.REJECTED, body.note if body else None,
        reason_code=code,
    )


@router.post("/{intervention_id}/modify", dependencies=LOCKED, response_model=InterventionActionOut)
async def modify(
    intervention_id: uuid.UUID, body: ModifyRequest, session: SessionDep, bus: BusDep, actor: ActorDep
):
    """Human edits the proposed change; the edited change is what gets authorized (status=modified)."""
    if not body.modified_change:
        raise ApiError("modified_change must not be empty", status_code=422, error_type="validation_error")
    return await _decide(
        session, bus, intervention_id, actor, ApprovalStatus.MODIFIED, body.note, body.modified_change,
        executor=body.executor, review_reason=body.review_reason,
    )


@router.post("/{intervention_id}/execute", dependencies=LOCKED, response_model=InterventionActionOut, status_code=202)
async def execute(
    intervention_id: uuid.UUID,
    session: SessionDep,
    bus: BusDep,
    actor: ActorDep,
    body: ExecuteRequest | None = None,
):
    """Activate the approved intervention's experiment with the chosen executor (default: manual).

    Manual: issues (or re-returns) the intervention package; a human then calls POST .../executed.
    GitHub (optional, only if explicitly requested AND configured): queued as a job.
    """
    iv, inc = await _load(session, intervention_id)
    state = S(inc.state)
    if state != S.APPROVED:
        raise IllegalTransitionError(
            f"cannot execute while the incident is {state.value}; approval is required first",
            {"state": state.value},
        )
    try:
        await appr.require_executable_approval(session, iv)
    except appr.ApprovalError as exc:
        raise _approval_error(exc) from exc
    which = _check_executor(iv.action, body.executor if body else None, body.dry_run if body else None)
    iv_id, inc_id = iv.id, inc.id
    payload: dict[str, Any] = {"intervention_id": str(iv_id), "requested_by": actor, "executor": which}
    if body and body.dry_run is not None:
        payload["dry_run"] = body.dry_run
    await audit(
        session, "human", actor, "intervention", iv_id, "intervention.execute_requested", payload, commit=True
    )
    job = None
    message = None
    if which == "manual":
        message = await _activate_manual(session, iv_id)
        if message:
            raise Conflict(message, {"executor": "manual"})
    else:
        try:
            job_id = await enqueue("execute_intervention", payload)
        except QueueUnavailable as exc:
            await bus.emit(
                None, inc_id, "execution_queued", StepStatus.FAILED, "Job queue unavailable", {"error": str(exc)}
            )
            raise
        session.expire_all()
        inc = await svc.get_incident(session, inc_id)
        if S(inc.state) == S.APPROVED:  # the worker may already have advanced it
            try:
                _step(inc, S.EXECUTING, actor, "execution queued",
                      {"intervention_id": str(iv_id), "job_id": str(job_id)})
                await session.commit()
            except IllegalTransitionError:
                await session.rollback()  # lost the race to the worker, which owns the transition
        await bus.emit(
            None, inc_id, "execution_queued", StepStatus.RUNNING, "Execution queued",
            {"intervention_id": str(iv_id), "job_id": str(job_id), "executor": which},
        )
        job = job_id
    session.expire_all()
    iv = await session.get(Intervention, iv_id)
    inc = await svc.get_incident(session, inc_id)
    out = await intervention_out(session, iv)
    return InterventionActionOut(
        intervention=out,
        incident_state=str(inc.state),
        job=svc.job_ref(await session.get(Job, job)) if job else None,
        experiment_id=out.experiment_id,
        message=message,
    )


@router.post("/{intervention_id}/executed", dependencies=LOCKED, response_model=ExecutedOut)
async def executed(
    intervention_id: uuid.UUID,
    body: RecordExecutionRequest,
    session: SessionDep,
    actor: ActorDep,
):
    """A HUMAN records that they applied the manual intervention package. This is a real execution: it starts the
    verification window at `executed_at`. 409 if already recorded or the incident is not approved; 422 for a
    future `executed_at`; 403 for a non-human actor."""
    from app.services import pipeline

    iv, _ = await _load(session, intervention_id)
    iv_id = iv.id
    actor_type = "system" if actor.lower() in manual_exec.NON_HUMAN_ACTORS else "human"
    try:
        res = await pipeline.record_executed(
            session, iv_id, actor, executed_at=body.executed_at, reference_url=body.reference_url,
            note=body.note, actual_change=body.actual_change, actor_type=actor_type,
        )
    except appr.HumanActorRequired as exc:
        raise ApiError(str(exc), status_code=403, error_type="forbidden") from exc
    except manual_exec.InvalidExecutionTime as exc:
        raise ApiError(str(exc), status_code=422, error_type="validation_error") from exc
    except manual_exec.AlreadyExecuted as exc:
        raise Conflict(str(exc), {"reason": "already_executed"}) from exc
    except (manual_exec.NotExecutable, appr.ApprovalError, IllegalTransition) as exc:
        raise IllegalTransitionError(str(exc), {"reason": type(exc).__name__}) from exc
    session.expire_all()
    iv = await session.get(Intervention, iv_id)
    inc = await svc.get_incident(session, iv.incident_id)
    exp = await _experiment_for(session, iv_id)
    return ExecutedOut(
        intervention=await intervention_out(session, iv),
        incident_state=str(inc.state),
        experiment_id=exp.id if exp else None,
        execution_id=uuid.UUID(res["execution_id"]),
        executed_at=exp.executed_at if exp else None,
        verification_window_start=exp.verification_window_start if exp else None,
        deviation=res["deviation"],
    )
