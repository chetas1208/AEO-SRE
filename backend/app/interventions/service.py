"""Glue for workers/API: gate, run the selected executor (default: manual), persist an `Execution` row, advance the experiment ledger.

Order of operations (fail closed):
1. `require_executable_approval` (observe exempt) -> ExecutionRefused if no APPROVED/MODIFIED human approval;
2. executor runs `effective_change(approval, intervention)` (the human's modification when MODIFIED);
3. `Execution` row persisted (status awaiting_human_execution|planned|succeeded|failed);
4. on success/plan: `begin_execution` + `mark_executed` on the experiment; non-retryable failure: `fail_experiment`;
   retryable failure leaves experiment + approval untouched so the job can simply be retried.
Incident state transitions stay with the pipeline (A14)."""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ExperimentStatus
from app.experiments.ledger import begin_execution, fail_experiment, mark_executed
from app.interventions.executor import (
    ExecutionRefused,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    select_executor,
)
from app.models.core import Incident
from app.models.interventions import Approval, Execution, Experiment, Intervention
from app.services.approvals import ApprovalError, latest_approval, require_executable_approval

log = structlog.get_logger()


async def _policy_version(session: AsyncSession, intervention: Intervention) -> str | None:
    if not intervention.policy_version_id:
        return None
    from app.models.policy import PolicyVersion

    pv = await session.get(PolicyVersion, intervention.policy_version_id)
    return pv.version if pv else None


async def _experiment_for(session: AsyncSession, intervention_id: uuid.UUID) -> Experiment | None:
    q = select(Experiment).where(Experiment.intervention_id == intervention_id).order_by(
        Experiment.created_at.desc())
    return (await session.execute(q)).scalars().first()


async def run_execution(
    session: AsyncSession, intervention_id: uuid.UUID, *, dry_run: bool | None = None,
    executor: Executor | None = None, choice: str | None = None, evidence: list[Any] | None = None,
) -> tuple[ExecutionResult, Execution]:
    """Execute an approved intervention. Raises ExecutionRefused (nothing persisted or mutated) when the approval
    gate fails; otherwise persists an Execution row and returns (result, row)."""
    iv = await session.get(Intervention, intervention_id)
    if iv is None:
        raise LookupError(f"intervention {intervention_id} not found")
    try:
        approval: Approval | None = await require_executable_approval(session, iv)
    except ApprovalError as exc:
        raise ExecutionRefused(str(exc), "approval_required") from exc
    incident = await session.get(Incident, iv.incident_id)
    try:
        executor = executor or select_executor(iv.action, choice=choice, dry_run=dry_run)  # default: manual
    except ExecutionRefused:
        raise
    if evidence is None:
        from app.models.evidence import Evidence

        evidence = list((await session.execute(select(Evidence).where(Evidence.incident_id == iv.incident_id)
                                               .order_by(Evidence.created_at))).scalars())
    result = await executor.execute(iv, approval, incident, evidence=evidence,
                                    policy_version=await _policy_version(session, iv))
    row = Execution(
        intervention_id=iv.id, approval_id=approval.id if approval is not None else None, executor=result.executor,
        status=result.status.value, reference=result.reference, dry_run=result.dry_run, log=result.to_log(),
        started_at=result.started_at, finished_at=result.finished_at, error=result.error, package=result.package)
    session.add(row)
    await session.flush()
    await _advance_experiment(session, iv, row, result)
    await _audit(session, iv, row, result)
    log.info("executor.finished", intervention=str(iv.id), status=result.status.value, dry_run=result.dry_run,
             retryable=result.retryable)
    return result, row


async def _advance_experiment(session: AsyncSession, iv: Intervention, row: Execution, result: ExecutionResult) -> None:
    exp = await _experiment_for(session, iv.id)
    if exp is None:
        return
    status = ExperimentStatus(exp.status)
    if result.status is ExecutionStatus.AWAITING_HUMAN_EXECUTION:
        return  # a human still has to apply it: the experiment advances in `record_manual_execution`
    if result.status is ExecutionStatus.FAILED:
        if not result.retryable and status in (ExperimentStatus.APPROVED, ExperimentStatus.EXECUTING):
            await fail_experiment(session, exp, result.error or "execution failed", result.executor)
        return
    if status is ExperimentStatus.APPROVED:
        await begin_execution(session, exp, row)
        status = ExperimentStatus.EXECUTING
    if status is ExperimentStatus.EXECUTING:
        from app.experiments.window import window_from_settings

        await mark_executed(session, exp, row, window=window_from_settings())
    else:
        log.warning("executor.experiment_not_advanced", experiment=str(exp.id), status=status.value)


async def _audit(session: AsyncSession, iv: Intervention, row: Execution, result: ExecutionResult) -> None:
    try:
        from app.core.audit import audit

        await audit(session, "system", result.executor, "intervention", iv.id, f"execution.{result.status.value}",
                    {"execution_id": str(row.id), "dry_run": result.dry_run, "reference": result.reference,
                     "approver": result.approver, "error_code": result.error_code, "retryable": result.retryable,
                     "external_mutation": result.external_mutation})
    except Exception as exc:  # noqa: BLE001 - audit trail must not mask the execution outcome
        log.warning("executor.audit_failed", error=str(exc))


__all__ = ["ExecutionRefused", "ExecutionStatus", "latest_approval", "run_execution"]
