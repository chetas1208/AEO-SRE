"""Manual execution: the DEFAULT, always-available executor.

Profound Lift decides and explains; a human applies the change; the system records exactly what was applied and then
measures the outcome. `ManualExecutor` performs NO external mutation: it renders a structured INTERVENTION
PACKAGE (exact change / diff, target, steps, evidence summary, risk, rollback, observation window) onto an
`Execution` row with status `awaiting_human_execution`. A human then calls `record_manual_execution`, which
turns that row into a REAL execution (`dry_run=False`, executor `manual`) and starts verification.

A manual execution is a real execution for learning: rewards, verification and outcome ranges all count it.
Only a GitHub-executor preview is ever a dry-run.

`ProfoundAgentExecutor` runs live Profound published agents when `PROFOUND_API_KEY` is set.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import get_settings
from app.domain.enums import ActionType, ApprovalStatus, ExperimentStatus, IncidentState
from app.experiments import ledger
from app.experiments.window import window_from_settings
from app.incidents import state_machine
from app.interventions.changes import PAGE_KINDS, ProposedChange, build_diff, diff_file
from app.interventions.executor import (
    ExecutionRefused,
    ExecutionResult,
    ExecutionStatus,
    ExecutorCapability,
    check_gate,
)
from app.models.core import Incident
from app.models.interventions import Execution, Experiment, Intervention
from app.services.approvals import (
    ApprovalError,
    HumanActorRequired,
    effective_change,
    require_executable_approval,
)

log = structlog.get_logger()
S = IncidentState
MANUAL = "manual"
FUTURE_TOLERANCE = timedelta(seconds=60)  # client clock skew only
HUMAN_ACTOR_TYPES = ("human",)
NON_HUMAN_ACTORS = frozenset({"system", "pipeline", "model", "llm", "worker", "agent", "bot", "scheduler"})


def _v(x: Any) -> str:
    return str(getattr(x, "value", x))


def _f(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


# ---------------------------------------------------------------------------------------------------------
# errors


class ManualExecutionError(Exception):
    """Base class for refusals of `record_manual_execution` (nothing was changed)."""


class AlreadyExecuted(ManualExecutionError):
    """The execution was already recorded (idempotency guard; surfaces as HTTP 409)."""


class NotExecutable(ManualExecutionError):
    """Precondition not met (no approval, wrong incident state, observe action, no experiment...)."""


class InvalidExecutionTime(ManualExecutionError):
    """executed_at is in the future."""


# ---------------------------------------------------------------------------------------------------------
# package


def _verification_window() -> dict[str, Any]:
    delay = float(get_settings().verification_delay_hours)
    duration = ledger.DEFAULT_WINDOW.duration
    return {
        "starts": "at the time you record the execution, plus the Profound data lag",
        "delay_hours": delay,
        "duration_days": duration.days,
        "note": "Profound data lags 24-48h; measurements taken before the delay elapses are not counted.",
    }


def _rollback_text(change: ProposedChange, action: ActionType) -> str:
    if action == ActionType.OBSERVE:
        return "Nothing to roll back: no change is made."
    if change.kind in PAGE_KINDS and change.files:
        parts = []
        for fc in change.files:
            if fc.change_type == "create":
                parts.append(f"unpublish or remove the new page {fc.path}")
            elif fc.section_id:
                parts.append(f"remove the block delimited by the aeo-sre:{fc.section_id} markers in {fc.path} "
                             "(or restore the previous version)")
            else:
                parts.append(f"restore the previous version of {fc.path}")
        return "To roll back: " + "; ".join(parts) + "."
    if action == ActionType.PUBLISHER_OUTREACH:
        return "An email cannot be unsent; send a follow-up retracting the request if circumstances change."
    if action == ActionType.STRUCTURED_DATA:
        return "Remove the added JSON-LD block from the page and redeploy."
    return "Revert the applied change using your normal change-management process."


def _steps(change: ProposedChange, action: ActionType) -> list[str]:
    close = "Come back here and click 'Mark as executed' with the time you applied it (and a reference URL, if any)."
    if action == ActionType.OBSERVE:
        return list(change.manual_task.checklist) if change.manual_task else ["Keep monitoring the affected prompts."]
    if change.kind in PAGE_KINDS and change.files:
        steps = []
        for fc in change.files:
            verb = "Create" if fc.change_type == "create" else "Update"
            target = change.target_url or fc.path
            steps.append(f"{verb} {target} (file/path: {fc.path}) with the proposed text below, exactly as written.")
        steps += ["Publish / deploy through your usual process.",
                  "Open the live page and confirm the change is visible.", close]
        return steps
    if change.manual_task is not None:
        return [*change.manual_task.checklist, close]
    return ["Apply the proposed change.", close]


def _evidence_summary(evidence: list[Any] | None, limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for e in (evidence or [])[:limit]:
        out.append({
            "title": _f(e, "title") or _f(e, "url") or str(_f(e, "id")),
            "url": _f(e, "url"), "type": _v(_f(e, "type")), "status": _v(_f(e, "status")),
        })
    return out


def build_package(
    intervention: Any, approval: Any | None, incident: Any | None = None, *, evidence: list[Any] | None = None,
    policy_version: str | None = None,
) -> dict[str, Any]:
    """Pure: the structured intervention package for `effective_change(approval, intervention)`."""
    action = ActionType(_v(intervention.action))
    raw = effective_change(approval, intervention)
    if not raw or not raw.get("kind"):
        raise ExecutionRefused("this intervention has no proposed change to hand over", "no_proposed_change")
    try:
        change = ProposedChange.model_validate(raw)
    except Exception as exc:
        raise ExecutionRefused(f"proposed change is malformed: {exc}", "invalid_change") from exc
    files = []
    for fc in change.files:
        files.append({
            "path": fc.path, "target_url": change.target_url, "change_type": fc.change_type,
            "patch_mode": fc.patch_mode, "section_id": fc.section_id,
            "proposed_text": fc.new_content, "current_content": fc.old_content,
            "current_content_known": fc.old_content is not None,
            "diff": diff_file(fc),
        })
    task = change.manual_task.model_dump(mode="json") if change.manual_task is not None else None
    target_url = change.target_url or (task or {}).get("target_url")
    modified = approval is not None and _v(getattr(approval, "status", "")) == "modified"
    return {
        "version": 1,
        "executor": MANUAL,
        "action": action.value,
        "kind": change.kind,
        "title": change.title or intervention.title,
        "summary": change.summary,
        "target": {"url": target_url, "paths": [f["path"] for f in files]},
        "changes": files,
        "diff": build_diff(change.files) if change.files else "",
        "manual_task": task,
        "steps": _steps(change, action),
        "why": intervention.rationale or change.summary,
        "evidence_summary": _evidence_summary(evidence),
        "fact_ids": list(change.fact_ids),
        "risk": _v(intervention.risk),
        "rollback": _rollback_text(change, action),
        "observation_window": _verification_window(),
        "approved_by": getattr(approval, "decided_by", None),
        "approval_status": _v(approval.status) if approval is not None else None,
        "modified_by_human": modified,
        "policy_version": policy_version,
        "incident_id": str(getattr(incident, "id", intervention.incident_id)),
        "intervention_id": str(intervention.id),
        "notes": list(change.notes),
        "requires_human_step": action != ActionType.OBSERVE,
        "generated_at": datetime.now(UTC).isoformat(),
    }


class ManualExecutor:
    """Default executor. No external mutation, ever: hands a human the exact change and waits."""

    name = MANUAL

    def capability(self) -> ExecutorCapability:
        return ExecutorCapability(
            name=self.name, label="Manual", state="healthy", available=True, mutates_external=False, default=True,
            detail="always available: Profound Lift prepares the exact change, a human applies and records it")

    async def execute(self, intervention: Any, approval: Any | None, incident: Any | None = None, *,
                      evidence: list[Any] | None = None, policy_version: str | None = None) -> ExecutionResult:
        check_gate(intervention, approval, incident)  # fail closed: human approval required (observe exempt)
        started = datetime.now(UTC)
        package = build_package(intervention, approval, incident, evidence=evidence, policy_version=policy_version)
        action = ActionType(_v(intervention.action))
        common = dict(executor=self.name, dry_run=False, external_mutation=False, package=package,
                      approval_status=_v(approval.status) if approval is not None else None,
                      approver=getattr(approval, "decided_by", None), diff=package["diff"], started_at=started)
        if action == ActionType.OBSERVE:  # no human step: activation == observing
            return ExecutionResult(
                status=ExecutionStatus.SUCCEEDED, reference=f"observe:{intervention.id}", artifact=package["manual_task"],
                log=["observe: no change is made; the experiment is now observing (verification window started)"],
                finished_at=datetime.now(UTC), **common)
        return ExecutionResult(
            status=ExecutionStatus.AWAITING_HUMAN_EXECUTION, reference=None, artifact=package["manual_task"],
            log=["intervention package prepared; no external system was modified; awaiting a human to apply it "
                 "and record the execution"],
            finished_at=datetime.now(UTC), **common)


class ProfoundAgentExecutor:
    """Runs published Profound agents via POST /v1/agents/{id}/runs when PROFOUND_API_KEY is set."""

    name = "profound_agent"

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings

    def capability(self) -> ExecutorCapability:
        from app.core.config import get_settings
        from app.services.profound_agents import profound_generation_enabled

        s = self._settings or get_settings()
        if profound_generation_enabled():
            return ExecutorCapability(
                name=self.name,
                label="Profound agent",
                state="healthy",
                available=True,
                mutates_external=True,
                detail="POST /v1/agents/{id}/runs (live published agents)",
            )
        if s.profound_api_key:
            return ExecutorCapability(
                name=self.name,
                label="Profound agent",
                state="unavailable",
                available=False,
                mutates_external=True,
                detail="PROFOUND_AGENT_RUNS_ENABLED=0 or agent runs disabled",
            )
        return ExecutorCapability(
            name=self.name,
            label="Profound agent",
            state="unavailable",
            available=False,
            mutates_external=True,
            detail="PROFOUND_API_KEY not set",
        )

    async def execute(self, intervention: Any, approval: Any | None, incident: Any | None = None, *,
                      evidence: list[Any] | None = None, policy_version: str | None = None) -> ExecutionResult:
        from app.connectors.profound.client import ProfoundClient
        from app.connectors.profound.errors import ProfoundError
        from app.services.profound_agents import (
            build_run_inputs,
            profound_generation_enabled,
            resolve_agent_ids,
        )

        if not profound_generation_enabled():
            raise ExecutionRefused("the Profound agent executor is not available", "executor_unavailable")
        if approval is None or _v(getattr(approval, "status", None)) != ApprovalStatus.APPROVED.value:
            raise ExecutionRefused("Profound agent runs require an approved intervention", "approval_required")

        pc = getattr(intervention, "proposed_change", None) or {}
        if not isinstance(pc, dict):
            pc = {}
        requested = list(pc.get("profound_agent_ids") or [])
        if pc.get("profound_agent_id"):
            requested.insert(0, str(pc["profound_agent_id"]))

        from app.services.profound_agents import list_profound_agents

        catalog = (await list_profound_agents(limit=50)).get("agents") or []
        agent_ids = resolve_agent_ids(requested, catalog)
        if not agent_ids:
            raise ExecutionRefused("no published Profound agents available to run", "no_agents")

        ctx = {
            "name": getattr(intervention, "title", None),
            "hypothesis": getattr(intervention, "rationale", None),
            "target_url": pc.get("target_url"),
            "experiment_id": pc.get("experiment_id"),
            "campaign_id": pc.get("campaign_id"),
        }
        inputs = build_run_inputs("intervention", ctx)
        client = ProfoundClient()
        logs: list[str] = []
        run_refs: list[str] = []
        try:
            for aid in agent_ids[:3]:
                resp = await client.run_agent(aid, inputs=inputs or None)
                data = resp.data if isinstance(resp.data, dict) else {}
                rid = data.get("id")
                if rid:
                    run_refs.append(f"profound:{aid}:{rid}")
                    logs.append(f"started Profound agent {aid} run {rid}")
        except ProfoundError as exc:
            raise ExecutionRefused(f"Profound agent run failed: {type(exc).__name__}", "profound_run_failed") from exc
        finally:
            await client.aclose()

        started = datetime.now(UTC)
        ref = run_refs[0] if run_refs else None
        return ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            reference=ref,
            artifact={"profound_runs": run_refs, "agent_ids": agent_ids},
            log=logs or ["Profound agent run accepted"],
            started_at=started,
            finished_at=datetime.now(UTC),
            executor=self.name,
            dry_run=False,
            approval_status=_v(approval.status),
            approver=getattr(approval, "decided_by", None),
        )


def executor_capabilities(settings: Any = None) -> dict[str, ExecutorCapability]:
    """Capability report for every known executor (no network)."""
    from app.interventions.executor import GitHubPRExecutor

    return {c.name: c for c in (ManualExecutor().capability(),
                                GitHubPRExecutor(settings=settings).capability(),
                                ProfoundAgentExecutor(settings=settings).capability())}


# ---------------------------------------------------------------------------------------------------------
# persistence helpers


async def latest_execution(session: AsyncSession, intervention_id: uuid.UUID) -> Execution | None:
    q = select(Execution).where(Execution.intervention_id == intervention_id).order_by(
        Execution.created_at.desc(), Execution.id.desc()).limit(1)
    return (await session.execute(q)).scalars().first()


async def pending_manual_execution(session: AsyncSession, intervention_id: uuid.UUID) -> Execution | None:
    row = await latest_execution(session, intervention_id)
    if row is not None and row.executor == MANUAL and row.status == ExecutionStatus.AWAITING_HUMAN_EXECUTION.value:
        return row
    return None


async def _experiment_for(session: AsyncSession, intervention_id: uuid.UUID) -> Experiment | None:
    q = select(Experiment).where(Experiment.intervention_id == intervention_id).order_by(
        Experiment.created_at.desc()).limit(1)
    return (await session.execute(q)).scalars().first()


async def prepare_manual_package(session: AsyncSession, intervention_id: uuid.UUID) -> Execution:
    """Idempotently issue the package for an approved intervention (returns the existing row if present)."""
    from app.interventions.service import run_execution

    existing = await latest_execution(session, intervention_id)
    if existing is not None and existing.executor == MANUAL and existing.status in (
            ExecutionStatus.AWAITING_HUMAN_EXECUTION.value, ExecutionStatus.SUCCEEDED.value):
        return existing
    _, row = await run_execution(session, intervention_id, executor=ManualExecutor())
    return row


# ---------------------------------------------------------------------------------------------------------
# the human step


@dataclass
class ManualExecutionOutcome:
    execution: Execution
    experiment: Experiment
    incident: Incident
    deviation: bool
    executed_at: datetime
    transitions: list[dict[str, Any]]


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def record_manual_execution(
    session: AsyncSession,
    intervention_id: uuid.UUID,
    executed_by: str,
    executed_at: datetime | None = None,
    reference_url: str | None = None,
    note: str | None = None,
    actual_change: str | None = None,
    *,
    actor_type: str = "human",
    now: datetime | None = None,
) -> ManualExecutionOutcome:
    """A HUMAN reports that they applied the packaged change.

    Marks the Execution succeeded (`dry_run=False`, executor `manual`), stores `actual_change` verbatim (flagging a
    deviation from the proposal), starts the experiment's verification window at `executed_at` (honouring the
    Profound lag delay) and moves the incident approved -> executing -> executed -> awaiting_verification through
    the state machine. Idempotent: a second call raises `AlreadyExecuted`. Flushes; the caller commits.
    """
    if actor_type not in HUMAN_ACTOR_TYPES:
        raise HumanActorRequired(f"recording an execution requires a human actor, got {actor_type!r}")
    if not executed_by or not executed_by.strip() or executed_by.strip().lower() in NON_HUMAN_ACTORS:
        raise HumanActorRequired("recording an execution requires a named human (executed_by)")
    executed_by = executed_by.strip()
    now = _aware(now) if now else datetime.now(UTC)
    executed_at = _aware(executed_at) if executed_at else now
    if executed_at > now + FUTURE_TOLERANCE:
        raise InvalidExecutionTime(f"executed_at {executed_at.isoformat()} is in the future")

    iv = (await session.execute(select(Intervention).where(Intervention.id == intervention_id)
                                .with_for_update().execution_options(populate_existing=True))).scalars().first()
    if iv is None:
        raise LookupError(f"intervention {intervention_id} not found")
    if ActionType(iv.action) == ActionType.OBSERVE:
        raise NotExecutable("observe has no human step: activating the experiment starts observation")
    inc = await session.get(Incident, iv.incident_id, populate_existing=True)
    state = S(inc.state)
    if state in (S.EXECUTED, S.AWAITING_VERIFICATION, S.VERIFIED, S.REWARDED, S.CLOSED):
        raise AlreadyExecuted(f"execution for intervention {iv.id} was already recorded (incident is {state.value})")
    if state not in (S.APPROVED, S.EXECUTING):
        raise NotExecutable(f"incident is {state.value}; the intervention must be approved before it can be executed")
    try:
        approval = await require_executable_approval(session, iv)
    except ApprovalError as exc:
        raise NotExecutable(str(exc)) from exc

    row = await latest_execution(session, iv.id)
    if row is not None and row.status == ExecutionStatus.SUCCEEDED.value:
        raise AlreadyExecuted(f"execution {row.id} was already recorded as succeeded")
    if row is not None and row.executor != MANUAL and row.status != ExecutionStatus.FAILED.value:
        raise NotExecutable(f"this intervention is being executed by '{row.executor}'; it is not a manual execution")
    exp = await _experiment_for(session, iv.id)
    if exp is None:
        raise NotExecutable("no experiment is open for this intervention; refusing to record an untracked execution")
    if ExperimentStatus(exp.status) in (ExperimentStatus.EXECUTED, ExperimentStatus.AWAITING_VERIFICATION,
                                        ExperimentStatus.VERIFIED, ExperimentStatus.REWARDED):
        raise AlreadyExecuted(f"experiment {exp.code} was already executed")
    if row is None or row.status != ExecutionStatus.AWAITING_HUMAN_EXECUTION.value:
        row = await prepare_manual_package(session, iv.id)  # lazily issue the package (idempotent)
        if row.status == ExecutionStatus.SUCCEEDED.value:
            raise AlreadyExecuted(f"execution {row.id} was already recorded as succeeded")

    deviation = bool(actual_change and actual_change.strip())
    row.status = ExecutionStatus.SUCCEEDED.value
    row.dry_run = False
    row.executor = MANUAL
    row.reference = (reference_url or "").strip() or None
    row.executed_by = executed_by
    row.executed_at = executed_at
    row.finished_at = executed_at
    row.note = note
    row.deviation = deviation
    row.actual_change = {"text": actual_change, "recorded_by": executed_by,
                         "recorded_at": now.isoformat()} if deviation else None
    row.error = None
    row.log = [*(row.log or []), {"line": f"recorded by {executed_by} as executed at {executed_at.isoformat()}"
                                          + ("; DEVIATION from the proposal: actual change recorded verbatim"
                                             if deviation else "")}]
    await session.flush()

    # experiment ledger: approved -> executing -> executed -> awaiting_verification (window anchored at executed_at)
    if ExperimentStatus(exp.status) == ExperimentStatus.PROPOSED:
        await ledger.attach_approval(session, exp, approval)
    window = window_from_settings()
    if ExperimentStatus(exp.status) == ExperimentStatus.APPROVED:
        await ledger.begin_execution(session, exp, row, now=executed_at)
    await ledger.mark_executed(session, exp, row, executed_at=executed_at, window=window)
    if deviation:
        cur = exp.status.value if hasattr(exp.status, "value") else str(exp.status)
        exp.timeline = [*(exp.timeline or []), {
            "from": cur, "to": cur,
            "actor": executed_by, "at": now.isoformat(),
            "reason": "deviation: the human applied a change different from the approved proposal "
                      "(see execution.actual_change)"}]

    # incident: approved -> executing -> executed -> awaiting_verification
    records: list[dict[str, Any]] = []
    steps = [(S.EXECUTING, "manual execution reported by a human")] if S(inc.state) == S.APPROVED else []
    steps += [(S.EXECUTED, f"{executed_by} applied the change" + (" (with deviation)" if deviation else "")),
              (S.AWAITING_VERIFICATION, "waiting for post-intervention observation")]
    for dst, reason in steps:
        rec = state_machine.transition(inc, dst, executed_by, reason, now=now,
                                       metadata={"intervention_id": str(iv.id), "executor": MANUAL,
                                                 "reference": row.reference, "deviation": deviation})
        records.append(rec)
        await audit(session, "human", executed_by, "incident", inc.id, "state_transition", rec)
    await audit(session, "human", executed_by, "intervention", iv.id, "execution.recorded", {
        "execution_id": str(row.id), "executor": MANUAL, "executed_at": executed_at.isoformat(),
        "reference_url": row.reference, "note": note, "deviation": deviation, "dry_run": False,
        "actual_change": actual_change if deviation else None})
    await session.flush()
    log.info("manual.execution_recorded", intervention=str(iv.id), by=executed_by, deviation=deviation)
    return ManualExecutionOutcome(execution=row, experiment=exp, incident=inc, deviation=deviation,
                                  executed_at=executed_at, transitions=records)


__all__ = [
    "AlreadyExecuted", "InvalidExecutionTime", "ManualExecutionError", "ManualExecutionOutcome", "ManualExecutor",
    "NotExecutable", "ProfoundAgentExecutor", "build_package", "executor_capabilities", "latest_execution", "pending_manual_execution", "prepare_manual_package", "record_manual_execution",
]
