"""Incident read model: list/detail assembly, server-side status/CTA/allowed-action computation."""

import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import adapters
from app.api.errors import NotFound
from app.core.db import utcnow
from app.domain.enums import ActionType, IncidentState
from app.incidents import state_machine
from app.incidents.explanation import build_explanation
from app.models.core import Incident, IncidentEvent, Job, PromptCluster, Signal
from app.models.evidence import Evidence, Hypothesis
from app.models.interventions import Experiment, Intervention
from app.models.policy import PolicyDecision
from app.schemas.common import JobRef
from app.schemas.incidents import (
    CTA,
    AllowedAction,
    ExpectedOutcome,
    IncidentDetail,
    IncidentList,
    IncidentSummary,
    MetricDelta,
    PriorityBreakdown,
    PriorityComponent,
)

S = IncidentState

STATUS_BY_STATE: dict[str, str] = {
    S.DETECTED: "detected",
    S.TRIAGED: "detected",
    S.INVESTIGATING: "investigating",
    S.EVIDENCE_READY: "investigating",
    S.ROOT_CAUSE_PROPOSED: "needs_review",
    S.ROOT_CAUSE_CONFIRMED: "needs_review",
    S.INTERVENTION_PROPOSED: "ready_for_action",
    S.AWAITING_APPROVAL: "ready_for_action",
    S.APPROVED: "ready_for_action",
    S.EXECUTING: "executing",
    S.EXECUTED: "executing",
    S.AWAITING_VERIFICATION: "awaiting_measurement",
    S.VERIFIED: "verified",
    S.REWARDED: "verified",
    S.CLOSED: "resolved",
    S.DISMISSED: "dismissed",
    S.FAILED: "failed",
}
DISPLAY_LABELS: dict[str, str] = {
    "detected": "Detected",
    "investigating": "Investigating",
    "needs_review": "Needs Review",
    "ready_for_action": "Ready for Action",
    "executing": "Executing",
    "awaiting_measurement": "Awaiting Measurement",
    "verified": "Verified",
    "resolved": "Resolved",
    "dismissed": "Dismissed",
    "failed": "Failed",
}
RANGES = {"1h": 1 / 24, "24h": 1, "7d": 7, "14d": 14, "30d": 30, "90d": 90}
INVESTIGATABLE = {S.DETECTED, S.TRIAGED, S.EVIDENCE_READY, S.ROOT_CAUSE_PROPOSED}


def status_for(state: str) -> str:
    return STATUS_BY_STATE.get(str(state), "detected")


def states_for_status(status: str) -> list[str]:
    """Accept a UI status OR a raw state name."""
    by_status = [str(s) for s, st in STATUS_BY_STATE.items() if st == status]
    if by_status:
        return by_status
    return [status]


_NUM_RE = re.compile(r"^(?:INC-?)?(\d+)$", re.I)


async def get_incident(session: AsyncSession, ident: str | uuid.UUID | int) -> Incident:
    """Resolve by UUID, incident number (1042) or INC-1042."""
    inc = None
    if isinstance(ident, uuid.UUID):
        inc = await session.get(Incident, ident)
    else:
        text = str(ident).strip()
        try:
            inc = await session.get(Incident, uuid.UUID(text))
        except ValueError:
            m = _NUM_RE.match(text)
            if m:
                inc = (
                    await session.execute(select(Incident).where(Incident.number == int(m.group(1))))
                ).scalar()
    if inc is None:
        raise NotFound(f"incident {ident} not found")
    return inc


def normalize_metric(m: Any) -> MetricDelta | None:
    if not isinstance(m, dict):
        return None
    before, after, value, delta = (m.get(k) for k in ("before", "after", "value", "delta"))
    if delta is None and isinstance(before, int | float) and isinstance(after, int | float):
        delta = after - before
    pct = None
    if isinstance(before, int | float) and before and isinstance(after, int | float):
        pct = (after - before) / abs(before) * 100
    name = f"{m.get('key') or ''} {m.get('label') or m.get('name') or m.get('metric') or ''}".lower()
    lower_better = any(w in name for w in ("competitor", "position", "lost", "inaccura", "conflict"))
    favorable = None if delta is None or delta == 0 else ((delta < 0) == lower_better)
    return MetricDelta(
        favorable=m.get("favorable", favorable),
        label=str(m.get("label") or m.get("name") or m.get("metric") or m.get("key") or "metric"),
        key=m.get("key") or m.get("metric"),
        before=before,
        after=after,
        value=value,
        delta=delta,
        unit=m.get("unit"),
        delta_pct=m.get("delta_pct", pct),
    )


def metrics_of(inc: Incident) -> list[MetricDelta]:
    out = [normalize_metric(m) for m in (inc.metrics or [])]
    return [m for m in out if m is not None][:4]


def primary_metric(inc: Incident) -> MetricDelta | None:
    for raw in inc.metrics or []:
        if isinstance(raw, dict) and raw.get("primary"):
            return normalize_metric(raw)
    ms = metrics_of(inc)
    return ms[0] if ms else None


async def _topics_and_trends(
    session: AsyncSession, incidents: list[Incident]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, list[float]]]:
    cluster_ids = {i.prompt_cluster_id for i in incidents if i.prompt_cluster_id}
    topics: dict[uuid.UUID, str] = {}
    trends: dict[uuid.UUID, list[float]] = {}
    if not cluster_ids:
        return topics, trends
    rows = (
        await session.execute(
            select(PromptCluster.id, PromptCluster.topic).where(PromptCluster.id.in_(cluster_ids))
        )
    ).all()
    topics = {r[0]: r[1] for r in rows}
    sigs = (
        await session.execute(
            select(Signal.prompt_cluster_id, Signal.metric, Signal.value, Signal.observed_at)
            .where(Signal.prompt_cluster_id.in_(cluster_ids))
            .order_by(Signal.observed_at)
        )
    ).all()
    series: dict[tuple[uuid.UUID, str], list[float]] = {}
    for cid, metric, value, _ in sigs:
        series.setdefault((cid, metric), []).append(float(value))
    for inc in incidents:
        if not inc.prompt_cluster_id:
            continue
        wanted = (inc.context or {}).get("trend_metric")
        pm = primary_metric(inc)
        candidates = [wanted, pm.key if pm else None, pm.label if pm else None]
        chosen = next(
            (
                series[(inc.prompt_cluster_id, c)]
                for c in candidates
                if c and (inc.prompt_cluster_id, c) in series
            ),
            None,
        )
        if chosen is None:
            mine = [v for (cid, _), v in series.items() if cid == inc.prompt_cluster_id]
            chosen = max(mine, key=len) if mine else []
        trends[inc.id] = chosen[-14:]
    return topics, trends


async def to_summaries(session: AsyncSession, incidents: list[Incident]) -> list[IncidentSummary]:
    topics, trends = await _topics_and_trends(session, incidents)
    out = []
    for inc in incidents:
        topic = topics.get(inc.prompt_cluster_id) or (inc.context or {}).get("topic")
        label = (inc.context or {}).get("context_label") or (
            " · ".join(p for p in [(inc.context or {}).get("persona"), topic] if p) or None
        )
        out.append(
            IncidentSummary(
                id=inc.id,
                number=inc.number,
                title=inc.title,
                severity=inc.severity,
                state=inc.state,
                status=status_for(inc.state),
                display_state=DISPLAY_LABELS[status_for(inc.state)],
                category=inc.category,
                priority=inc.priority,
                detected_at=inc.detected_at,
                first_observed_at=inc.first_observed_at,
                org_id=inc.org_id,
                topic=topic,
                context_label=label,
                primary_delta=primary_metric(inc),
                trend=trends.get(inc.id, []),
                investigation_status=inc.investigation_status,
            )
        )
    return out


def _filters(
    org_id: uuid.UUID | None,
    severity: list[str] | None,
    status: list[str] | None,
    topic: str | None,
    range_: str | None,
    q: str | None,
    skip: str | None = None,
):
    conds = []
    if org_id:
        conds.append(Incident.org_id == org_id)
    if severity and skip != "severity":
        conds.append(Incident.severity.in_([s.lower() for s in severity]))
    if status and skip != "status":
        states = [x for st in status for x in states_for_status(st.lower())]
        conds.append(Incident.state.in_(states))
    if topic:
        conds.append(
            Incident.prompt_cluster_id.in_(
                select(PromptCluster.id).where(func.lower(PromptCluster.topic) == topic.lower())
            )
        )
    if range_ and range_ in RANGES:
        conds.append(Incident.detected_at >= utcnow() - timedelta(days=RANGES[range_]))
    if q:
        conds.append(Incident.title.ilike(f"%{q}%"))
    return and_(*conds) if conds else None


async def list_incidents(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    severity: list[str] | None,
    status: list[str] | None,
    topic: str | None,
    range_: str | None,
    q: str | None,
    limit: int,
    offset: int,
    sort: str = "priority",
) -> IncidentList:
    where = _filters(org_id, severity, status, topic, range_, q)
    base = select(Incident)
    if where is not None:
        base = base.where(where)
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    order = (
        [Incident.detected_at.desc()]
        if sort == "detected"
        else [Incident.priority.desc(), Incident.detected_at.desc()]
    )
    rows = (await session.execute(base.order_by(*order).limit(limit).offset(offset))).scalars().all()

    async def grouped(column, skip: str):
        w = _filters(org_id, severity, status, topic, range_, q, skip=skip)
        stmt = select(column, func.count()).group_by(column)
        if w is not None:
            stmt = stmt.where(w)
        return (await session.execute(stmt)).all()

    sev = {r[0]: r[1] for r in await grouped(Incident.severity, "severity")}
    sev_counts = {
        "all": sum(sev.values()),
        **{k: sev.get(k, 0) for k in ("critical", "high", "medium", "low")},
    }
    states = {r[0]: r[1] for r in await grouped(Incident.state, "status")}
    status_counts: dict[str, int] = {"all": sum(states.values())}
    for st, n in states.items():
        status_counts[status_for(st)] = status_counts.get(status_for(st), 0) + n
    topic_rows = (
        (
            await session.execute(
                select(PromptCluster.topic)
                .where(
                    PromptCluster.id.in_(
                        select(Incident.prompt_cluster_id).where(Incident.prompt_cluster_id.is_not(None))
                    )
                )
                .distinct()
                .order_by(PromptCluster.topic)
            )
        )
        .scalars()
        .all()
    )
    return IncidentList(
        items=await to_summaries(session, list(rows)),
        total=total,
        limit=limit,
        offset=offset,
        counts_by_severity=sev_counts,
        counts_by_status=status_counts,
        counts_by_state=states,
        topics=list(topic_rows),
    )


def priority_breakdown(inc: Incident) -> PriorityBreakdown:
    raw = dict(inc.priority_breakdown or {})
    comps_raw = raw.get("components", raw)
    comps: list[PriorityComponent] = []
    if isinstance(comps_raw, dict):
        for key, v in comps_raw.items():
            if key in ("score", "method", "components"):
                continue
            if isinstance(v, dict):
                val = v.get("display", v.get("value"))
                if v.get("display") is None and isinstance(val, int | float) and val <= 1:
                    val = val * 100
                comps.append(
                    PriorityComponent(
                        key=key,
                        label=v.get("label") or key.replace("_", " ").capitalize(),
                        value=val,
                        weight=v.get("weight"),
                        source=v.get("source"),
                        note=v.get("note"),
                    )
                )
            elif isinstance(v, int | float):
                comps.append(
                    PriorityComponent(key=key, label=key.replace("_", " ").capitalize(), value=float(v))
                )
    return PriorityBreakdown(score=inc.priority, components=comps, method=raw.get("method"), raw=raw)


async def active_job(session: AsyncSession, inc: Incident, kind: str = "investigate_incident") -> Job | None:
    stmt = (
        select(Job)
        .where(Job.kind == kind, Job.payload["incident_id"].as_string() == str(inc.id))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar()


def job_ref(job: Job) -> JobRef:
    return JobRef(job_id=job.id, kind=job.kind, status=job.status, error=job.error)


async def intervention_rows(session: AsyncSession, incident_id: uuid.UUID) -> list[Any]:
    """Raw Intervention rows (A5 model), [] when the module has not landed."""
    mod = adapters.interventions_models()
    if mod is None or not hasattr(mod, "Intervention"):
        return []
    rows = await session.execute(select(mod.Intervention).where(mod.Intervention.incident_id == incident_id))
    return list(rows.scalars().all())


def _val(x: Any) -> str:
    return str(getattr(x, "value", x))


async def compute_actions(
    session: AsyncSession, inc: Incident, job: Job | None
) -> tuple[CTA, list[AllowedAction], uuid.UUID | None]:
    state = S(inc.state)
    running = (job is not None and job.status in ("queued", "running")) or (
        job is None and inc.investigation_status == "running"
    )
    interventions = await intervention_rows(session, inc.id)
    selected = next((i for i in interventions if getattr(i, "selected", False)), None) or (
        interventions[0] if interventions else None
    )
    sel_id = getattr(selected, "id", None)
    actionable = selected is not None and _val(getattr(selected, "action", "")) != ActionType.OBSERVE.value

    can = state_machine.can_transition
    allowed: list[AllowedAction] = []

    def add(action: str, enabled: bool, reason: str | None = None):
        allowed.append(AllowedAction(action=action, enabled=enabled, reason=None if enabled else reason))

    stalled = state == S.INVESTIGATING and not running  # job failed or worker died: allow a retry
    inv_ok = (state in INVESTIGATABLE or stalled) and not running
    add(
        "investigate",
        inv_ok,
        "investigation already running" if running else f"not available while {state.value}",
    )
    pending_approval = state in (S.INTERVENTION_PROPOSED, S.AWAITING_APPROVAL) and selected is not None
    add("approve", pending_approval, "no intervention awaiting approval")
    add("reject", pending_approval, "no intervention awaiting approval")
    add("modify", pending_approval and actionable, "no modifiable intervention awaiting approval")
    pending_manual = False
    if state == S.APPROVED and selected is not None:
        from app.interventions.manual import pending_manual_execution

        pending_manual = (await pending_manual_execution(session, selected.id)) is not None
    add("execute", state == S.APPROVED and not pending_manual, "intervention must be approved first")
    add("record_execution", pending_manual, "no manual intervention package is waiting to be applied")
    add("verify", state in (S.EXECUTED, S.AWAITING_VERIFICATION), "nothing executed to verify")
    add("resolve", state in (S.VERIFIED, S.REWARDED), "resolve is available after verification")
    add("dismiss", can(state, S.DISMISSED), "incident is in a terminal state")

    if state in (S.DETECTED, S.TRIAGED):
        cta = CTA(
            key="investigate",
            label="Investigate",
            enabled=not running,
            reason="investigation queued" if running else None,
        )
    elif state == S.INVESTIGATING and stalled:
        cta = CTA(
            key="investigate", label="Retry investigation", enabled=True, reason="no active investigation job"
        )
    elif state == S.INVESTIGATING:
        cta = CTA(
            key="none", label="Investigation running", enabled=False, reason="investigation in progress"
        )
    elif state in (S.EVIDENCE_READY, S.ROOT_CAUSE_PROPOSED):
        cta = CTA(key="review", label="Review evidence", enabled=True, target_tab="evidence")
    elif state in (S.ROOT_CAUSE_CONFIRMED, S.INTERVENTION_PROPOSED, S.AWAITING_APPROVAL):
        if pending_approval:
            label = "Approve" if actionable else "Approve observation"
            cta = CTA(
                key="approve", label=label, enabled=True, intervention_id=sel_id, target_tab="action"
            )
        else:
            cta = CTA(
                key="none",
                label="Awaiting intervention proposal",
                enabled=False,
                reason="no intervention proposed yet",
            )
    elif state == S.APPROVED and pending_manual:
        cta = CTA(
            key="mark_executed",
            label="Mark as executed",
            enabled=True,
            reason="the intervention package is ready: apply it, then record when you did",
            intervention_id=sel_id,
            target_tab="action",
        )
    elif state == S.APPROVED:
        cta = CTA(
            key="execute",
            label="Activate experiment",
            enabled=True,
            intervention_id=sel_id,
            target_tab="action",
        )
    elif state == S.EXECUTING:
        cta = CTA(key="none", label="Executing", enabled=False, reason="executor running")
    elif state in (S.EXECUTED, S.AWAITING_VERIFICATION):
        cta = CTA(
            key="none",
            label="Awaiting Measurement",
            enabled=False,
            reason="awaiting measurement",
        )
    elif state in (S.VERIFIED, S.REWARDED):
        cta = CTA(key="resolve", label="Resolve", enabled=True)
    else:
        cta = CTA(key="none", label=state.value.replace("_", " ").capitalize(), enabled=False)
    return cta, allowed, sel_id


async def expected_outcome(session: AsyncSession, inc: Incident, action: Any = None) -> ExpectedOutcome:
    """Historical range from verified experiments (same incident class + action); never a forecast."""
    from app.experiments.outcomes import historical_outcome_range

    if action is None:
        rows = await intervention_rows(session, inc.id)
        sel = next((r for r in rows if getattr(r, "selected", False)), None) or (rows[0] if rows else None)
        action = getattr(sel, "action", None)
    if action is None:
        return ExpectedOutcome(available=False, reason="No candidate intervention yet.")
    try:
        res = await historical_outcome_range(session, inc.category, action)
    except ValueError:  # category outside the fixed vocabulary
        res = None
    if res is None:
        return ExpectedOutcome(available=False, reason="Insufficient experiment history to estimate outcome.")
    pm = primary_metric(inc)
    candidates = (pm.key, pm.label) if pm else ()
    key = next((k for k in candidates if k in res.metrics), None) or next(iter(res.metrics), None)
    rng = res.metrics.get(key) if key else None
    if rng is None:
        return ExpectedOutcome(
            available=False, n=res.n, reason="No comparable metric in similar experiments."
        )
    return ExpectedOutcome(
        available=True,
        n=res.n,
        low=rng.low,
        high=rng.high,
        metric=key,
        unit="fraction",
        reason=f"Historical range from similar verified experiments (n={res.n}); {res.basis}.",
    )


async def incident_detail(session: AsyncSession, inc: Incident) -> IncidentDetail:
    (summary,) = await to_summaries(session, [inc])
    job = await active_job(session, inc)
    cta, allowed, _ = await compute_actions(session, inc, job)
    prompt_count = 0
    if inc.prompt_cluster_id:
        cluster = await session.get(PromptCluster, inc.prompt_cluster_id)
        prompt_count = len(cluster.prompts or []) if cluster else 0
    experiment_id = None
    window = None
    imod = adapters.interventions_models()
    if imod is not None and hasattr(imod, "Experiment"):
        experiment = (
            await session.execute(
                select(Experiment)
                .where(Experiment.incident_id == inc.id)
                .order_by(Experiment.created_at.desc())
                .limit(1)
            )
        ).scalar()
        if experiment is not None:
            experiment_id = experiment.id
            start, end = experiment.verification_window_start, experiment.verification_window_end
            if start and end:
                window = f"{start.isoformat()} to {end.isoformat()}"
    selected = (
        await session.execute(
            select(Intervention)
            .where(Intervention.incident_id == inc.id, Intervention.selected.is_(True))
            .limit(1)
        )
    ).scalar()
    alternatives = list((
        await session.execute(
            select(Intervention)
            .where(Intervention.incident_id == inc.id)
            .order_by(Intervention.score.desc().nullslast())
            .limit(3)
        )
    ).scalars().all())
    if selected is not None and all(row.id != selected.id for row in alternatives):
        alternatives.append(selected)
    decision = (
        await session.execute(
            select(PolicyDecision)
            .where(PolicyDecision.incident_id == inc.id)
            .order_by(PolicyDecision.created_at.desc())
            .limit(1)
        )
    ).scalar()
    constrained = bool((decision.meta or {}).get("constrained_to_observe")) if decision is not None else False
    score_label = "unconstrained policy probability" if constrained else "policy score"
    hypotheses = (
        await session.execute(select(Hypothesis).where(Hypothesis.incident_id == inc.id))
    ).scalars().all()
    scores = [
        {"action": str(row.action.value if hasattr(row.action, "value") else row.action),
         "policy_score": row.score, "label": score_label, "selected": bool(row.selected)}
        for row in alternatives
    ]
    families = [
        str(row) for row in (
            await session.execute(select(Evidence.type).where(Evidence.incident_id == inc.id))
        ).scalars().all()
    ]
    events = (
        await session.execute(
            select(IncidentEvent.stage, IncidentEvent.at)
            .where(IncidentEvent.incident_id == inc.id)
            .order_by(IncidentEvent.at)
        )
    ).all()
    started = next((at for stage, at in events if stage == "investigation.started"), None)
    timings: dict[str, float | None] = {}
    if started is not None:
        for label, stage in (
            ("evidence_ready_seconds", "evidence.web.completed"),
            ("root_cause_seconds", "gate.completed"),
            ("intervention_proposed_seconds", "approval.pending"),
        ):
            hit = next((at for name, at in events if name == stage), None)
            timings[label] = None if hit is None else round((hit - started).total_seconds(), 1)
    explanation = build_explanation(
        title=inc.title,
        priority=inc.priority,
        metrics=metrics_of(inc),
        hypotheses=list(hypotheses),
        hypotheses_meta=(inc.context or {}).get("hypotheses_meta"),
        selected_action=(str(selected.action.value if hasattr(selected.action, "value") else selected.action)
                         if selected is not None else None),
        policy_scores=scores,
        verification_window=window,
        selection_note=(decision.meta or {}).get("reason") if constrained and decision is not None else None,
        timings=timings,
        evidence_families=families,
    )
    return IncidentDetail(
        **summary.model_dump(),
        summary=inc.summary or "",
        confidence=inc.confidence,
        metrics=metrics_of(inc),
        priority_breakdown=priority_breakdown(inc),
        primary_cta=cta,
        allowed_actions=allowed,
        allowed_next_states=sorted(state_machine.next_states(inc.state), key=lambda s: s.value),
        affected_prompt_count=prompt_count,
        active_job=job_ref(job) if job and job.status in ("queued", "running", "failed") else None,
        expected_outcome=await expected_outcome(session, inc),
        experiment_id=experiment_id,
        context=inc.context or {},
        explanation=explanation,
    )
