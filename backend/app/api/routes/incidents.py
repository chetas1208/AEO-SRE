import json
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, Query, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api import adapters
from app.api.deps import ActorDep, BusDep, PageDep, SessionDep
from app.api.errors import Conflict, IllegalTransitionError, NotFound
from app.core.audit import audit
from app.core.event_types import canonical_event_type
from app.core.queue import QueueUnavailable, enqueue
from app.domain.enums import IncidentState, StepStatus
from app.evidence import graph as graph_lib
from app.incidents import state_machine
from app.incidents.state_machine import IllegalTransition
from app.models.core import Incident, IncidentEvent, Job, Organization, PromptCluster
from app.models.evidence import Evidence, EvidenceEdge, EvidenceNode, Hypothesis
from app.schemas.common import JobRef
from app.schemas.evidence import EvidenceDetail, EvidenceItem, GraphOut, HypothesisOut
from app.schemas.incidents import (
    DetectOut,
    DetectRequest,
    EvidenceListOut,
    IncidentDetail,
    IncidentEventOut,
    IncidentList,
    InvestigateOut,
    PromptRow,
    PromptsOut,
    TransitionOut,
    TransitionRequest,
)
from app.services import incidents as svc

log = structlog.get_logger()
router = APIRouter(prefix="/api/incidents", tags=["incidents"])
S = IncidentState


def _split(values: list[str] | None) -> list[str] | None:
    out = [p.strip() for v in values or [] for p in v.split(",") if p.strip()]
    return out or None


@router.get("", response_model=IncidentList)
async def list_incidents(
    session: SessionDep,
    page: PageDep,
    org_id: uuid.UUID | None = None,
    severity: Annotated[
        list[str] | None, Query(description="critical|high|medium|low; repeat or comma-separate")
    ] = None,
    status: Annotated[
        list[str] | None, Query(description="UI status or raw state; repeat or comma-separate")
    ] = None,
    topic: str | None = None,
    range: Annotated[str | None, Query(description="1h|24h|7d|14d|30d|90d")] = None,
    q: str | None = None,
    sort: Annotated[str, Query(pattern="^(priority|detected)$")] = "priority",
):
    return await svc.list_incidents(
        session,
        org_id=org_id,
        severity=_split(severity),
        status=_split(status),
        topic=topic,
        range_=range,
        q=q,
        limit=page.limit,
        offset=page.offset,
        sort=sort,
    )


@router.post("/detect", response_model=DetectOut, status_code=202)
async def detect(session: SessionDep, actor: ActorDep, body: DetectRequest | None = None):
    org_ids = [body.org_id] if body and body.org_id else None
    if org_ids is None:
        org_ids = list((await session.execute(select(Organization.id))).scalars().all())
    elif await session.get(Organization, org_ids[0]) is None:
        raise NotFound(f"organization {org_ids[0]} not found")
    jobs: list[JobRef] = []
    for oid in org_ids:
        try:
            jid = await enqueue("detect_incidents", {"org_id": str(oid)})
            job = await session.get(Job, jid)
            await session.refresh(job)
            jobs.append(JobRef(job_id=jid, kind="detect_incidents", status=job.status, error=job.error))
        except QueueUnavailable as exc:
            jobs.append(JobRef(kind="detect_incidents", status="unavailable", error=str(exc)))
    await audit(
        session,
        "human",
        actor,
        "incident",
        "detect",
        "detect.requested",
        {"org_ids": [str(o) for o in org_ids]},
        commit=True,
    )
    return DetectOut(jobs=jobs)


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(incident_id: str, session: SessionDep):
    return await svc.incident_detail(session, await svc.get_incident(session, incident_id))


@router.post("/{incident_id}/investigate", response_model=InvestigateOut, status_code=202)
async def investigate(incident_id: str, session: SessionDep, bus: BusDep, actor: ActorDep):
    inc = await svc.get_incident(session, incident_id)
    job = await svc.active_job(session, inc)
    if job is not None and job.status in ("queued", "running"):
        raise Conflict("investigation already running", {"job_id": str(job.id), "status": job.status})
    if S(inc.state) not in svc.INVESTIGATABLE | {S.INVESTIGATING}:
        raise IllegalTransitionError(
            f"cannot investigate an incident in state {inc.state}", {"state": inc.state}
        )
    inc_id, inc_number = inc.id, inc.number
    await audit(
        session, "human", actor, "incident", inc_id, "investigation.requested", {"number": inc_number}
    )
    inc.investigation_status = "queued"
    await session.commit()
    await bus.emit(
        None,
        inc_id,
        "investigation_queued",
        StepStatus.PENDING,
        "Investigation queued",
        {"requested_by": actor},
    )
    try:
        job_id = await enqueue("investigate_incident", {"incident_id": str(inc_id)})
    except QueueUnavailable as exc:
        inc = await svc.get_incident(session, inc_id)
        inc.investigation_status = "not_started"
        await session.commit()
        await bus.emit(
            None,
            inc_id,
            "investigation_queued",
            StepStatus.FAILED,
            "Job queue unavailable",
            {"error": str(exc)},
        )
        raise
    session.expire_all()
    inc = await svc.get_incident(session, inc_id)
    job = await session.get(Job, job_id)
    return InvestigateOut(incident_id=inc.id, job=svc.job_ref(job), state=inc.state)


async def _manual_transition(
    session, inc: Incident, dst: S, actor: str, reason: str, event: str
) -> TransitionOut:
    src = S(inc.state)
    try:
        rec = state_machine.transition(inc, dst, actor, reason)
    except IllegalTransition as exc:
        raise IllegalTransitionError(str(exc), {"from": src.value, "to": dst.value}) from exc
    await audit(
        session, "human", actor, "incident", inc.id, event, {k: v for k, v in rec.items() if k != "at"}
    )
    await session.commit()
    return TransitionOut(incident_id=inc.id, from_state=src, to_state=dst)


@router.post("/{incident_id}/resolve", response_model=TransitionOut)
async def resolve(
    incident_id: str, session: SessionDep, actor: ActorDep, body: TransitionRequest | None = None
):
    inc = await svc.get_incident(session, incident_id)
    reason = (body.reason if body and body.reason else None) or "resolved by operator after verification"
    return await _manual_transition(session, inc, S.CLOSED, actor, reason, "incident.resolved")


@router.post("/{incident_id}/dismiss", response_model=TransitionOut)
async def dismiss(
    incident_id: str, session: SessionDep, actor: ActorDep, body: TransitionRequest | None = None
):
    inc = await svc.get_incident(session, incident_id)
    reason = (body.reason if body and body.reason else None) or "dismissed by operator"
    return await _manual_transition(session, inc, S.DISMISSED, actor, reason, "incident.dismissed")


@router.get("/{incident_id}/evidence", response_model=EvidenceListOut, response_model_by_alias=False)
async def evidence(
    incident_id: str,
    session: SessionDep,
    type: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    inc = await svc.get_incident(session, incident_id)
    stmt = select(Evidence).where(Evidence.incident_id == inc.id)
    if type:
        stmt = stmt.where(Evidence.type == type)
    if status:
        stmt = stmt.where(Evidence.status == status)
    rows = (await session.execute(stmt.order_by(Evidence.observed_at.desc().nulls_last()))).scalars().all()
    all_items = [EvidenceItem.model_validate(r) for r in rows]
    items = all_items[offset : offset + limit]
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for it in all_items:  # counts describe the whole set, not the page
        by_type[it.type.value] = by_type.get(it.type.value, 0) + 1
        by_status[it.status.value] = by_status.get(it.status.value, 0) + 1
    return EvidenceListOut(
        incident_id=inc.id, items=items, total=len(all_items), counts_by_type=by_type, counts_by_status=by_status,
        limit=limit, offset=offset,
    )


async def _graph_inputs(session, inc: Incident):
    async def rows(model):
        return (await session.execute(select(model).where(model.incident_id == inc.id))).scalars().all()

    return await rows(Evidence), await rows(EvidenceEdge), await rows(Hypothesis), await rows(EvidenceNode)


@router.get(
    "/{incident_id}/evidence/{evidence_id}", response_model=EvidenceDetail, response_model_by_alias=False
)
async def evidence_detail(incident_id: str, evidence_id: uuid.UUID, session: SessionDep):
    inc = await svc.get_incident(session, incident_id)
    ev, edges, hyps, nodes = await _graph_inputs(session, inc)
    row = next((e for e in ev if e.id == evidence_id), None)
    if row is None:
        raise NotFound(f"evidence {evidence_id} not found on incident {inc.number}")
    return graph_lib.evidence_detail(row, graph_lib.build_graph(inc, ev, edges, hyps, nodes))


@router.get("/{incident_id}/graph", response_model=GraphOut, response_model_by_alias=False)
async def graph(incident_id: str, session: SessionDep):
    inc = await svc.get_incident(session, incident_id)
    ev, edges, hyps, nodes = await _graph_inputs(session, inc)
    return graph_lib.build_graph(inc, ev, edges, hyps, nodes).serialize()


@router.get("/{incident_id}/hypotheses", response_model=list[HypothesisOut], response_model_by_alias=False)
async def hypotheses(incident_id: str, session: SessionDep):
    inc = await svc.get_incident(session, incident_id)
    ev, edges, hyps, nodes = await _graph_inputs(session, inc)
    g = graph_lib.build_graph(inc, ev, edges, hyps, nodes)
    ordered = sorted(hyps, key=lambda h: (h.confidence is None, -(h.confidence or 0)))
    return [graph_lib.hypothesis_out(h, g) for h in ordered]


def _prompt_row(p: Any, topic: str | None) -> PromptRow:
    if isinstance(p, str):
        return PromptRow(prompt=p, topic=topic)
    if not isinstance(p, dict):
        return PromptRow(prompt=str(p), topic=topic)
    engines = p.get("engines") or p.get("models") or ([p["engine"]] if p.get("engine") else [])
    known = {
        "prompt", "text", "query", "intent", "volume", "our_visibility", "visibility", "competitor",
        "competitor_visibility", "engines", "models", "engine", "change", "delta", "unit", "persona",
    }  # fmt: skip
    return PromptRow(
        prompt=str(p.get("prompt") or p.get("text") or p.get("query") or ""),
        intent=p.get("intent"),
        volume=p.get("volume"),
        our_visibility=p.get("our_visibility", p.get("visibility")),
        competitor=p.get("competitor"),
        competitor_visibility=p.get("competitor_visibility"),
        engines=[str(e) for e in engines],
        change=p.get("change", p.get("delta")),
        unit=p.get("unit"),
        persona=p.get("persona"),
        topic=topic,
        meta={k: v for k, v in p.items() if k not in known},
    )


@router.get("/{incident_id}/prompts", response_model=PromptsOut)
async def prompts(incident_id: str, session: SessionDep, page: PageDep):
    inc = await svc.get_incident(session, incident_id)
    cluster = await session.get(PromptCluster, inc.prompt_cluster_id) if inc.prompt_cluster_id else None
    if cluster is None:
        return PromptsOut(
            incident_id=inc.id, items=[], total=0, unavailable_reason="incident has no linked prompt cluster"
        )
    rows = [_prompt_row(p, cluster.topic) for p in cluster.prompts or []]
    return PromptsOut(
        incident_id=inc.id,
        cluster_id=cluster.id,
        topic=cluster.topic,
        items=rows[page.offset : page.offset + page.limit],
        total=len(rows),
    )


@router.get("/{incident_id}/interventions")
async def interventions(incident_id: str, session: SessionDep):
    from app.api.mappers import intervention_out
    from app.schemas.interventions import InterventionsOut

    inc = await svc.get_incident(session, incident_id)
    if adapters.interventions_models() is None:
        return InterventionsOut(
            incident_id=inc.id, items=[], unavailable_reason="intervention module not installed"
        )
    rows = await svc.intervention_rows(session, inc.id)
    rows.sort(key=lambda r: (not getattr(r, "selected", False), -(getattr(r, "score", None) or 0)))
    items = [await intervention_out(session, r) for r in rows]
    selected = next((i for i in items if i.selected), None)
    return InterventionsOut(
        incident_id=inc.id,
        items=items,
        selection_basis=selected.selection_basis if selected else None,
        policy_version=selected.policy_version if selected else None,
        cold_start=selected.cold_start if selected else True,
    )


@router.get("/{incident_id}/events/history", response_model=list[IncidentEventOut])
async def event_history(
    incident_id: str, session: SessionDep, after_seq: int | None = None, limit: int = Query(500, le=2000)
):
    inc = await svc.get_incident(session, incident_id)
    stmt = select(IncidentEvent).where(IncidentEvent.incident_id == inc.id)
    if after_seq is not None:
        stmt = stmt.where(IncidentEvent.seq > after_seq)
    rows = (await session.execute(stmt.order_by(IncidentEvent.seq).limit(limit))).scalars().all()
    return [
        IncidentEventOut(
            id=r.id,
            seq=r.seq,
            incident_id=r.incident_id,
            timestamp=r.at,
            stage=r.stage,
            event_type=canonical_event_type(r.stage, r.status, r.metadata_ or {}),
            status=r.status,
            message=r.message,
            metadata=r.metadata_ or {},
        )
        for r in rows
    ]


@router.get("/{incident_id}/events", response_class=EventSourceResponse, responses={200: {"description": "Server-sent events", "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def events(
    incident_id: str,
    request: Request,
    session: SessionDep,
    bus: BusDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after_seq: int | None = None,
):
    """SSE stream (UI.md 54). SSE `id` = event seq; reconnects replay persisted events after it."""
    inc = await svc.get_incident(session, incident_id)
    inc_id = inc.id
    await session.close()  # do not hold a pooled connection for the life of the stream
    cursor: str | int | None = after_seq if after_seq is not None else last_event_id

    async def stream():
        async for ev in bus.subscribe(inc_id, cursor, heartbeat_seconds=15):
            if ev is None:
                continue
            yield {"id": str(ev["seq"]), "data": json.dumps(ev)}

    return EventSourceResponse(
        stream(), ping=15, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
