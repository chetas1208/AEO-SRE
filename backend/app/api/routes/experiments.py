import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import ActorDep, BusDep, PageDep, SessionDep
from app.api.errors import ApiError, Conflict, NotFound
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
from app.domain.enums import (
    ActionType,
    ApprovalStatus,
    ExperimentStatus,
    IncidentCategory,
    IncidentState,
    Severity,
)
from app.domain.errors import ExperimentNotVerifiable
from app.experiments import window as vwindow
from app.experiments.collision import find_collisions, scope_key
from app.experiments.spec import build_spec
from app.experiments.window import window_from_settings
from app.models.core import Incident, Job, Organization, PromptCluster
from app.models.interventions import Approval, Execution, Experiment, ExperimentOutcome, Intervention, Reward
from app.models.policy import PolicyVersion
from app.schemas.experiments import (
    DeclaredMetrics,
    ExperimentCreateIn,
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


@router.get("/baseline-preview")
async def experiment_baseline_preview(
    session: SessionDep,
    primary_metric: str = "visibility",
    org_id: uuid.UUID | None = None,
):
    """Live Profound (+ optional Mixpanel) baselines for the create-experiment UI."""
    from app.services.org_defaults import resolve_live_brand_org_id

    oid = org_id or await resolve_live_brand_org_id(session)
    if oid is None:
        return {"status": "NO_ORG", "metrics": {}, "primary_metric": primary_metric}
    from app.experiments.spec import resolve_primary_metric

    metrics = await _live_metric_baseline(session, oid, None)
    resolved = resolve_primary_metric(category=None, before=metrics, requested=primary_metric) if metrics else None
    primary_value = metrics.get(primary_metric) if metrics else None
    if primary_value is None and resolved and metrics:
        primary_value = metrics.get(resolved)
    return {
        "status": "OK" if metrics else "NO_SIGNALS",
        "organization_id": str(oid),
        "primary_metric": primary_metric,
        "resolved_primary_metric": resolved,
        "metrics": metrics,
        "primary_value": primary_value,
    }


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


async def _live_metric_baseline(session, org_id: uuid.UUID, cluster_id: uuid.UUID | None) -> dict[str, float]:
    """Refresh org signals and return the latest measured baseline (Profound path), or {} when none exist."""
    from app.services.pipeline import _numeric, ingest, metric_snapshot

    try:
        await ingest(session, org_id)
        await session.commit()
    except Exception:  # noqa: BLE001 — ingestion unavailable must not block experiment creation
        await session.rollback()
    snap = await metric_snapshot(session, org_id, cluster_id)
    return _numeric(snap)


def _normalize_run_mode(raw: str | None) -> str:
    mode = (raw or "live").strip().lower()
    return mode if mode in ("live", "test") else "live"


@router.post("", response_model=ExperimentDetail, status_code=201)
async def create_experiment(
    data: ExperimentCreateIn,
    session: SessionDep,
    actor: ActorDep,
    bus: BusDep,
):
    """Create a new experiment with full hypothesis, action, baseline metrics, target protection, and verification schedule."""
    from app.core.config import get_settings

    now = datetime.now(UTC)
    run_mode = _normalize_run_mode(data.run_mode)
    if run_mode == "test" and not get_settings().allow_test_run_mode:
        raise ApiError(
            "Sandbox run_mode is disabled on this deployment.",
            status_code=403,
            error_type="forbidden",
            code="TEST_RUN_MODE_DISABLED",
        )

    # 1. Resolve org_id (live Profound brand org, not arbitrary first row in Postgres)
    org_id = data.org_id
    if not org_id:
        if data.incident_id:
            existing_inc = await session.get(Incident, data.incident_id)
            if not existing_inc:
                raise NotFound(f"incident {data.incident_id} not found")
            org_id = existing_inc.org_id
        else:
            from app.services.org_defaults import resolve_live_brand_org_id

            org_id = await resolve_live_brand_org_id(session)
            if org_id is None:
                first_org = (await session.execute(select(Organization).limit(1))).scalar()
                if not first_org:
                    raise Conflict("No organization found to bind experiment")
                org_id = first_org.id

    # 2. Resolve or create Incident
    inc: Incident | None = None
    if data.incident_id:
        inc = await session.get(Incident, data.incident_id)
        if not inc:
            raise NotFound(f"incident {data.incident_id} not found")
    else:
        # Category mapped to primary metric for deterministic spec builder
        cat_map = {
            "visibility": IncidentCategory.VISIBILITY_DROP,
            "citation_share": IncidentCategory.LOST_CITATION_SOURCE,
            "accuracy": IncidentCategory.FACTUAL_CONFLICT,
            "competitor_share": IncidentCategory.COMPETITOR_CITATION_GAIN,
        }
        category = cat_map.get(data.primary_metric, IncidentCategory.FACTUAL_CONFLICT)
        cluster = PromptCluster(org_id=org_id, topic=data.name[:255], prompts=[])
        session.add(cluster)
        await session.flush()

        inc = Incident(
            org_id=org_id,
            prompt_cluster_id=cluster.id,
            title=data.name,
            category=category.value,
            severity=Severity.HIGH.value,
            priority=50.0,
            state=IncidentState.INTERVENTION_PROPOSED.value,
            detected_at=now,
            summary=f"Experiment candidate: {data.hypothesis[:200]}",
            context={
                "campaign_id": data.campaign_id,
                "hypothesis": data.hypothesis,
                "source": "live",
                "provenance": "profound",
                "run_mode": run_mode,
            },
            metrics=[],
        )
        session.add(inc)
        await session.flush()
        live_before = await _live_metric_baseline(session, org_id, None)
        if live_before:
            inc.metrics = [{"key": k, "before": v, "after": v, "source": "profound"} for k, v in live_before.items()]

    # 3. Resolve baseline metrics (measured signals first; never substitute demo fixtures for live runs)
    before = {}
    if not (inc.metrics or []):
        live_before = await _live_metric_baseline(session, org_id, None)
        if live_before:
            inc.metrics = [{"key": k, "before": v, "after": v, "source": "profound"} for k, v in live_before.items()]
    for m in (inc.metrics or []):
        if isinstance(m, dict) and m.get("key") and (m.get("before") is not None or m.get("after") is not None):
            before[m["key"]] = float(m.get("before") if m.get("before") is not None else m.get("after"))
    if not before:
        if run_mode == "test":
            before = {"visibility": 58.0, "citation_share": 24.0, "accuracy": 62.0, "competitor_share": 42.0}
        else:
            raise ApiError(
                "No measured baseline metrics are available for this org/cluster yet. "
                "Run signal ingestion or attach the experiment to an incident with measured metrics.",
                status_code=409,
                error_type="conflict",
                code="BASELINE_UNAVAILABLE",
            )
    from app.integrations.mixpanel.metrics import count_events, parse_mixpanel_metric_key
    from app.experiments.spec import resolve_primary_metric

    mp_event = parse_mixpanel_metric_key(data.primary_metric)
    if mp_event and data.primary_metric not in before:
        end = datetime.now(UTC)
        start = end - timedelta(days=7)
        before[data.primary_metric] = float(
            await count_events(session, org_id=org_id, event_name=mp_event, start=start, end=end)
        )

    effective_primary = resolve_primary_metric(
        category=inc.category, before=before, requested=data.primary_metric
    )
    if effective_primary is None:
        if run_mode == "test":
            before[data.primary_metric] = 50.0
            effective_primary = data.primary_metric
        else:
            raise ApiError(
                f"No measured baseline for primary metric '{data.primary_metric}' "
                "(and no fallback metrics are available for this org yet).",
                status_code=409,
                error_type="conflict",
                code="BASELINE_UNAVAILABLE",
            )
    elif effective_primary != (data.primary_metric or "").strip().lower() and mp_event is None:
        log.info(
            "experiment.primary_metric_fallback",
            requested=data.primary_metric,
            effective=effective_primary,
            incident_id=str(inc.id),
        )

    # 4. Resolve ActionType
    try:
        action = ActionType(data.selected_action.lower())
    except ValueError:
        raise ApiError(
            f"Invalid action '{data.selected_action}'. Must be one of {[a.value for a in ActionType]}",
            status_code=422,
            error_type="unprocessable_entity",
            code="INVALID_ACTION",
        )

    # 5. Create Intervention
    target_key = data.target_key
    if not target_key and data.target_url:
        target_key = f"target:{data.target_url.strip().lower().rstrip('/')}"
    if not target_key:
        target_key = scope_key(inc)

    iv = Intervention(
        id=uuid.uuid4(),
        incident_id=inc.id,
        action=action,
        title=data.name,
        rationale=data.hypothesis,
        proposed_change={
            "target_url": data.target_url,
            "target_key": target_key,
            "campaign_id": data.campaign_id,
            "notes": data.notes,
        },
        selection_basis="manual_override",
    )
    session.add(iv)
    await session.flush()

    # 6. Build spec & verification window (live = Profound lag; test = CI sandbox only)
    executed_at = now if data.auto_activate else None
    if run_mode == "test":
        delay_hours = 1.0
        if executed_at is not None:
            w_start = executed_at
            w_end = w_start + timedelta(hours=float(data.verification_window_hours))
        else:
            w_start = now + timedelta(hours=delay_hours)
            w_end = w_start + timedelta(hours=float(data.verification_window_hours))
    else:
        vw = window_from_settings()
        delay_hours = vw.delay.total_seconds() / 3600.0
        if executed_at is not None:
            w_start, w_end = vw.bounds(executed_at)
            w_end = w_start + timedelta(hours=float(data.verification_window_hours))
        else:
            w_start = now + vw.delay
            w_end = w_start + timedelta(hours=float(data.verification_window_hours))

    spec = build_spec(
        action=action,
        root_cause=data.hypothesis[:120],
        category=inc.category,
        before_metrics=before,
        window_start=w_start,
        window_end=w_end,
        executed_after=timedelta(hours=delay_hours),
        declared_at=now,
    ).to_json()

    exp_status = ExperimentStatus.PROPOSED
    if data.auto_activate:
        from app.incidents.state_machine import can_transition, transition as inc_transition

        for dst in (
            IncidentState.AWAITING_APPROVAL,
            IncidentState.APPROVED,
            IncidentState.EXECUTING,
            IncidentState.EXECUTED,
            IncidentState.AWAITING_VERIFICATION,
        ):
            if can_transition(inc.state, dst):
                inc_transition(inc, dst, actor or "human", "activated on experiment creation")
                await session.flush()

    # Check collision (contamination)
    dummy_exp = Experiment(
        id=uuid.uuid4(),
        incident_id=inc.id,
        intervention_id=iv.id,
        selected_action=action,
        status=ExperimentStatus.PROPOSED,
        target_key=target_key,
        dry_run=data.dry_run,
    )
    conflicts = await find_collisions(session, dummy_exp)
    if conflicts:
        raise Conflict(
            f"Target '{data.target_url or target_key}' is already protected by active experiment {conflicts[0].experiment_id} ({conflicts[0].reason}); "
            "wait for measurement to finish or dismiss the conflicting experiment.",
            {"conflicts": [{"experiment_id": str(c.experiment_id), "reason": c.reason, "status": c.status} for c in conflicts]},
        )

    # 7. Persist Experiment
    exp = Experiment(
        id=dummy_exp.id,
        incident_id=inc.id,
        intervention_id=iv.id,
        policy_version_id=None,
        policy_probability=None,
        context_vector=[],
        alternatives=[],
        evidence_snapshot={},
        before_metrics=before,
        after_metrics=None,
        status=ExperimentStatus.PROPOSED,
        verification_window_start=w_start,
        verification_window_end=w_end,
        executed_at=executed_at,
        selected_action=action,
        selection_basis=None,
        cold_start=False,
        reason=data.hypothesis,
        proposed_change=iv.proposed_change,
        approved_change=iv.proposed_change if data.auto_activate else None,
        timeline=[
            {"from": None, "to": ExperimentStatus.PROPOSED.value, "actor": actor or "human", "reason": "experiment created", "at": now.isoformat()},
        ],
        spec=spec,
        target_key=target_key,
        dry_run=data.dry_run,
    )
    session.add(exp)
    await session.flush()

    if data.auto_activate:
        approval = Approval(
            intervention_id=iv.id,
            status=ApprovalStatus.APPROVED,
            decided_by=actor or "operator",
            decided_at=now,
            decided_actor_type="human",
            requested_by=actor or "human",
            requested_actor_type="human",
            requested_change=iv.proposed_change or {},
            note="Authorized when the experiment was activated",
        )
        session.add(approval)
        await session.flush()
        exp.approval_id = approval.id
        exp.approver = approval.decided_by
        await session.flush()

    if data.auto_activate:
        from app.experiments.status import transition as exp_transition

        for dst in (
            ExperimentStatus.APPROVED,
            ExperimentStatus.EXECUTING,
            ExperimentStatus.EXECUTED,
            ExperimentStatus.AWAITING_VERIFICATION,
        ):
            exp_transition(exp, dst, actor or "human", "activated on creation", now=now)
            await session.flush()

    # 8. Emit SSE event
    try:
        await bus.publish({
            "type": "experiment.created",
            "experiment_id": str(exp.id),
            "code": exp.code,
            "status": exp.status.value,
            "selected_action": exp.selected_action.value,
            "target_key": exp.target_key,
            "primary_metric": data.primary_metric,
            "created_at": now.isoformat(),
        })
    except Exception as exc:
        log.warning("experiment.bus_publish_failed", error=str(exc))

    await audit(session, "human", actor, "experiment", exp.id, "experiment.created", {
        "name": data.name,
        "action": action.value,
        "primary_metric": data.primary_metric,
        "target_key": target_key,
        "campaign_id": data.campaign_id,
        "auto_activate": data.auto_activate,
    }, commit=True)

    if data.auto_activate and not exp.dry_run:
        try:
            await enqueue("verify_experiment", {"experiment_id": str(exp.id)})
        except Exception as exc:  # noqa: BLE001 — queue optional; worker sweep still picks it up
            log.warning("experiment.verify_enqueue_failed", experiment_id=str(exp.id), error=str(exc))

    if data.run_profound_agents and run_mode == "live":
        from app.services.profound_agents import enqueue_profound_generation

        await enqueue_profound_generation(
            scope="experiment",
            entity_id=str(exp.id),
            agent_ids=data.profound_agent_ids or [],
            context={
                "experiment_id": str(exp.id),
                "campaign_id": data.campaign_id,
                "name": data.name,
                "hypothesis": data.hypothesis,
                "primary_metric": data.primary_metric,
                "target_url": data.target_url,
                "notes": data.notes,
            },
        )

    return await get_experiment(str(exp.id), session)


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
            "source": "live",
            "provenance": "profound",
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
        verification=_verification_out(exp, inc),
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


def _verification_out(exp, inc: Incident | None = None) -> VerificationInfo | None:
    from app.measurement.validation_snapshot import effective_verification_start

    start = effective_verification_start(exp, inc) or exp.verification_window_start
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
    from app.measurement.validation_snapshot import effective_verification_start, is_validation_run

    exp = await _get(session, experiment_id)
    inc = await session.get(Incident, exp.incident_id)
    status = exp.status.value if hasattr(exp.status, "value") else str(exp.status)
    if is_validation_run(exp, inc) and status == ExperimentStatus.VERIFIED.value:
        from app.services import pipeline as exp_pipeline

        await exp_pipeline.reward(session, exp.id)
        await session.commit()
        job = Job(kind="calculate_reward", status="success", payload={"experiment_id": str(exp.id)}, attempts=1)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return VerifyOut(experiment_id=exp.id, job=svc.job_ref(job))
    if status not in VERIFIABLE:
        raise Conflict(
            f"experiment is {status}; only executed experiments can be verified", {"status": status}
        )
    if exp.dry_run:
        raise Conflict("dry-run executions changed nothing and cannot be verified", {"dry_run": True})
    exp_id = exp.id
    start = effective_verification_start(exp, inc) or exp.verification_window_start
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
    if is_validation_run(exp, inc):
        from app.services import pipeline as exp_pipeline

        result = await exp_pipeline.verify(session, exp_id, force=force)
        await session.commit()
        job = Job(
            kind="verify_experiment",
            status="success",
            payload={"experiment_id": str(exp_id), "force": force},
            result=result,
            attempts=1,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        if result.get("status") == "verified":
            await exp_pipeline.reward(session, exp_id)
            await session.commit()
        return VerifyOut(experiment_id=exp_id, job=svc.job_ref(job))
    job_id = await enqueue("verify_experiment", {"experiment_id": str(exp_id), "force": force})
    job = await session.get(Job, job_id)
    await session.refresh(job)
    return VerifyOut(experiment_id=exp_id, job=svc.job_ref(job))
