"""Pipeline orchestrator: ingest -> detect -> investigate -> propose -> (human approval) -> execute -> verify
-> reward -> policy update.

Each public coroutine is one job stage (see app.workers.jobs). Rules:
  * every step emits SSE events (app.core.events) and audit records; state changes go through the incident
    state machine only;
  * each investigation step is independently failure-tolerant (Plan section 7): a failed step is recorded on
    the incident (`context["investigation"]`) and as a FAILED event, the remaining steps still run, and the
    incident is marked `incomplete`; nothing is inferred to fill the gap;
  * stage functions are idempotent / resumable so a retried job never duplicates durable state;
  * nothing mutates the outside world without a human-decided approval (enforced in app.services.approvals
    and re-checked here before the executor runs).
"""
from __future__ import annotations

import importlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.experiments.guards  # noqa: F401  (registers the after_metrics write guard)
from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import utcnow
from app.core.events import emit as emit_event
from app.domain.enums import (
    ActionType,
    EdgeType,
    EvidenceStatus,
    EvidenceType,
    ExperimentStatus,
    HypothesisStatus,
    IncidentState,
    SelectionBasis,
    StepStatus,
)
from app.evidence.provenance import content_hash, edge_provenance, evidence_snapshot, extract_for
from app.experiments.collision import ExperimentCollision
from app.experiments.spec import SpecError
from app.incidents import state_machine
from app.incidents.detector import METRIC_ALIASES
from app.investigation.budget import BudgetTracker, InvestigationBudget, budget_exhausted, decide_stop
from app.models.core import Incident, Organization, PromptCluster, Signal
from app.models.evidence import Evidence, EvidenceEdge, EvidenceNode, Hypothesis, NodeKind

log = structlog.get_logger()
S = IncidentState
ACTOR = "pipeline"

INVESTIGABLE = {S.DETECTED, S.TRIAGED, S.INVESTIGATING, S.EVIDENCE_READY, S.ROOT_CAUSE_PROPOSED}
MAX_GATE_HYPOTHESES = 5
CONFIRMING_STATES = {S.ROOT_CAUSE_CONFIRMED, S.ROOT_CAUSE_PROPOSED}


class PermanentError(Exception):
    """Not retryable: the job payload or entity state can never succeed (recorded on the Job, no backoff)."""


# ---------------------------------------------------------------------------------------------------------
# small helpers


async def _emit(session: AsyncSession | None, incident_id: uuid.UUID, stage: str, status: StepStatus,
                message: str, meta: dict[str, Any] | None = None) -> None:
    """SSE + persisted event. Event delivery problems never break the pipeline."""
    try:
        await emit_event(session, incident_id, stage, status, message, meta or {})
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.emit_failed", stage=stage, error=repr(exc))
        if session is not None:
            await session.rollback()


async def _fresh(session: AsyncSession, model: type, ident: uuid.UUID) -> Any:
    return await session.get(model, ident, populate_existing=True)


async def _fresh_locked(session: AsyncSession, model: type, ident: uuid.UUID) -> Any:
    """Re-read and take a row lock (SELECT ... FOR UPDATE; a no-op on SQLite) until the next commit/rollback."""
    return await session.get(model, ident, populate_existing=True, with_for_update=True)


async def _move(session: AsyncSession, incident: Incident, dst: IncidentState, reason: str, *,
                actor: str = ACTOR, meta: dict[str, Any] | None = None, gate: Any = None) -> None:
    """State-machine transition + audit + SSE + commit."""
    record = state_machine.transition(incident, dst, actor, reason, metadata=meta, gate=gate)
    await audit(session, "system", actor, "incident", incident.id, "state_transition", record)
    await session.commit()
    await _emit(session, incident.id, "state", StepStatus.SUCCESS, f"{record['from_state']} -> {record['to_state']}",
                {"from": record["from_state"], "to": record["to_state"], "reason": reason})


async def _chain(kind: str, payload: dict[str, Any], run_inline: Callable[[], Awaitable[dict[str, Any]]],
                 session: AsyncSession, incident_id: uuid.UUID | None = None) -> str:
    """Enqueue the next stage; if the queue is down, run it inline so the loop does not stall. Returns how."""
    from app.workers.queue import QueueUnavailable, enqueue_job

    try:
        await enqueue_job(kind, payload)
        return "queued"
    except QueueUnavailable as exc:
        log.warning("pipeline.queue_unavailable_inline", kind=kind, error=str(exc))
        if incident_id:
            await _emit(session, incident_id, "queue", StepStatus.WARNING,
                        f"Job queue unavailable; running {kind} inline", {"error": str(exc)})
        await run_inline()
        return "inline"


def _optional(path: str, attr: str) -> Any | None:
    try:
        return getattr(importlib.import_module(path), attr)
    except (ImportError, AttributeError) as exc:
        log.info("pipeline.optional_missing", module=path, attr=attr, error=str(exc))
        return None


def _canon(metric: str | None, kind: str | None = None) -> str | None:
    for name in (metric, kind):
        key = (name or "").strip().lower()
        if key in METRIC_ALIASES:
            return METRIC_ALIASES[key]
    return None


async def metric_snapshot(session: AsyncSession, org_id: uuid.UUID, cluster_id: uuid.UUID | None, *,
                          start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """Latest measured value per canonical metric in [start, end] for the cluster (or org level).

    Returns {metric_key: value, "_observed_at": newest signal time, "_signal_ids": [...]}; empty when there
    is no data. Values are exactly what Profound reported (headline series preferred)."""
    stmt = select(Signal).where(Signal.org_id == org_id).order_by(Signal.observed_at)
    if cluster_id is not None:
        stmt = stmt.where(Signal.prompt_cluster_id == cluster_id)
    if start is not None:
        stmt = stmt.where(Signal.observed_at >= start)
    if end is not None:
        stmt = stmt.where(Signal.observed_at <= end)
    rows = (await session.execute(stmt)).scalars().all()
    latest: dict[str, tuple[bool, datetime, float, str, str]] = {}
    for s in rows:
        key = _canon(s.metric, s.kind)
        if key is None:
            continue
        headline = s.source == "profound"
        cur = latest.get(key)
        # prefer headline series; within a series, the newest observation
        if cur is None or (headline, s.observed_at) >= (cur[0], cur[1]):
            latest[key] = (headline, s.observed_at, float(s.value), str(s.id), s.source)
    if not latest:
        return {}
    out: dict[str, Any] = {k: v[2] for k, v in latest.items()}
    out["_observed_at"] = max(v[1] for v in latest.values()).isoformat()
    out["_signal_ids"] = [v[3] for v in latest.values()]
    out["_sources"] = sorted({v[4] for v in latest.values()})
    return out


def _numeric(snapshot: dict[str, Any]) -> dict[str, float]:
    return {k: v for k, v in snapshot.items() if not k.startswith("_") and isinstance(v, (int, float))}


# ---------------------------------------------------------------------------------------------------------
# ingest + detect


async def ingest(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Pull Profound signals for the org (raw + normalized), then trigger detection. Unavailable Profound is a
    recorded no-op, never fabricated data."""
    org = await session.get(Organization, org_id)
    if org is None:
        raise PermanentError(f"organization {org_id} not found")
    ingest_org = _optional("app.services.ingestion", "run_scheduled_ingest")
    if ingest_org is None:
        return {"status": "unavailable", "detail": "ingestion service not installed"}
    result = await ingest_org(session, org_id)
    await session.commit()
    summary = result.to_dict() if hasattr(result, "to_dict") else (result if isinstance(result, dict) else {})
    summary = {k: v for k, v in summary.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
    status = str(summary.get("status", "ok"))
    if status not in ("unavailable", "disabled", "failed", "error"):
        await _chain("detect_incidents", {"org_id": str(org_id)},
                     lambda: detect(session, org_id), session)
    return {"org_id": str(org_id), **summary}


async def detect(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Run the rolling-baseline detector; for each new incident record + emit, and queue investigation when
    it clears the action threshold (otherwise it stays `detected` for a human to triage)."""
    from app.incidents.detector import detect_incidents

    created = await detect_incidents(session, org_id)
    await session.commit()
    queued, below = [], []
    for inc in created:
        iid = inc.id
        await audit(session, "system", ACTOR, "incident", iid, "incident.detected",
                    {"title": inc.title, "category": str(inc.category), "priority": inc.priority})
        await session.commit()
        await _emit(session, iid, "detected", StepStatus.SUCCESS, f"Incident #{inc.number} detected: {inc.title}",
                    {"incident_id": str(iid), "number": inc.number, "severity": str(inc.severity),
                     "priority": inc.priority})
        if (inc.context or {}).get("below_action_threshold"):
            below.append(str(iid))
            await _emit(session, iid, "triage", StepStatus.WARNING,
                        "Priority is below the action threshold; not auto-investigated (observe)", {})
        elif get_settings().auto_investigate:
            await _chain("investigate_incident", {"incident_id": str(iid)},
                         lambda i=iid: investigate(session, i), session, iid)
            queued.append(str(iid))
    return {"org_id": str(org_id), "created": [str(i.id) for i in created], "investigation_queued": queued,
            "below_threshold": below}


# ---------------------------------------------------------------------------------------------------------
# investigation steps


@dataclass(frozen=True)
class MetricOrigin:
    """Where the measured series actually came from. `profound` only when every cited signal says so."""

    label: str
    from_profound: bool


async def _metric_origin(session: AsyncSession, inc: Incident) -> MetricOrigin:
    ids = (inc.context or {}).get("signal_ids") or []
    sources: set[str] = set()
    if ids:
        rows = (await session.execute(select(Signal.source).where(Signal.id.in_(ids)))).scalars().all()
        sources = {s for s in rows if s}
    if sources == {"profound"}:
        return MetricOrigin("profound", True)
    if not sources:
        return MetricOrigin("unknown", False)
    return MetricOrigin(",".join(sorted(sources)), False)


async def _profound_evidence(session: AsyncSession, inc: Incident, origin: MetricOrigin | None = None) -> int:
    """Turn the incident's measured metric changes into evidence rows (no inference; values as detected).

    The row type stays `profound` because that is the metric-change evidence class. The source and title
    name Profound only when the cited signals actually came from Profound.
    """
    origin = origin or await _metric_origin(session, inc)
    # Only the metric-change rows this function rebuilds; other PROFOUND evidence (e.g. discovery-gap perception
    # rows from factcheck/answers) must survive an investigation start.
    await session.execute(delete(Evidence).where(Evidence.incident_id == inc.id,
                                                 Evidence.type == EvidenceType.PROFOUND.value,
                                                 Evidence.retrieval_method.like("%.signal_series")))
    detection = {d.get("metric"): d for d in (inc.context or {}).get("detection", [])}
    signal_ids = (inc.context or {}).get("signal_ids", [])
    now = utcnow()
    prefix = "Profound" if origin.from_profound else "Measured"
    method = "profound.signal_series" if origin.from_profound else f"{origin.label}.signal_series"
    n = 0
    for m in inc.metrics or []:
        before, after, delta = m.get("before"), m.get("after"), m.get("delta")
        if before is None or after is None:
            continue
        key = m.get("key") or ""
        det = detection.get(key, {})
        unit = m.get("unit") or ""
        excerpt = (f"{m.get('label', key)} changed from {before}{'' if unit == 'count' else unit} to "
                   f"{after}{'' if unit == 'count' else unit} (delta {delta}) versus the rolling baseline.")
        session.add(Evidence(
            incident_id=inc.id, type=EvidenceType.PROFOUND.value, status=EvidenceStatus.LIVE.value,
            title=f"{prefix}: {m.get('label', key)} {before} -> {after}", source=origin.label,
            retrieval_method=method, retrieved_at=now,
            observed_at=inc.first_observed_at or inc.detected_at, excerpt=excerpt,
            content_hash=content_hash(f"{key}|{before}|{after}|{','.join(map(str, signal_ids))}"),
            support_score=inc.confidence, confidence=inc.confidence,
            raw={"kind": "metric_change", "metric": key, "before": before, "after": after, "delta": delta,
                 "delta_pct": m.get("delta_pct"), "unit": unit, "detection": det, "signal_ids": signal_ids,
                 "signal_source": origin.label, "from_profound": origin.from_profound,
                 "score_basis": "detector_confidence"},
        ))
        n += 1
    await session.flush()
    return n


async def stage_profound(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    inc = await _fresh(session, Incident, incident_id)
    origin = await _metric_origin(session, inc)
    n = await _profound_evidence(session, inc, origin)
    await session.commit()
    return {"profound_evidence": n, "metric_source": origin.label, "from_profound": origin.from_profound}


async def stage_answers(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Expensive answer rows, including citation details. Not part of scheduled ingest."""
    from app.investigation.answers import record_answer_details

    out = await record_answer_details(session, incident_id)
    await session.commit()
    return out


async def stage_fanout(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    from app.investigation.fanout import record_fanout_shifts

    # Stage-2 fetch (search queries): only now that an incident warrants it, and only when Profound is configured.
    deep: dict[str, Any] = {}
    if get_settings().profound_api_key:
        inc = await _fresh(session, Incident, incident_id)
        ingest_deep = _optional("app.services.ingestion", "ingest_deep")
        if inc is not None and ingest_deep is not None:
            try:
                res = await ingest_deep(session, inc.org_id)
                deep = {"deep_status": res.status, "deep_run_id": res.run_id}
            except Exception as exc:  # noqa: BLE001 - deep fetch is best effort; the shift check still runs
                deep = {"deep_status": "failed", "deep_error": type(exc).__name__}
    out = await record_fanout_shifts(session, incident_id)
    await session.commit()
    return {**out, **deep}


async def stage_collect(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Public-web evidence (own, canonical, competitor, cited pages). Failed fetches become unavailable
    evidence; the collector never infers page content."""
    from app.investigation.collector import collect_evidence

    inc = await _fresh(session, Incident, incident_id)

    async def emit(stage: str, status: StepStatus | str, message: str, meta: dict | None = None) -> None:
        await _emit(session, incident_id, stage, StepStatus(status) if isinstance(status, str) else status,
                    message, meta)

    rows = await collect_evidence(session, inc, emit, max_pages=get_settings().max_web_sources)
    await session.commit()
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1
    return {"web_evidence": len(rows), "by_status": by_status}


async def stage_rank(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """EvidenceRanker support/contradiction/insufficient/freshness for evidence the collector left unscored."""
    inc = await _fresh(session, Incident, incident_id)
    ranker_cls = _optional("app.evidence.ranker", "EvidenceRanker")
    if ranker_cls is None:
        return {"ranked": 0, "ranker": "unavailable"}
    ranker = ranker_cls.load()
    claim = f"{inc.title}. {inc.summary}".strip()
    rows = (await session.execute(
        select(Evidence).where(Evidence.incident_id == incident_id, Evidence.type != EvidenceType.PROFOUND.value,
                               Evidence.support_score.is_(None))
    )).scalars().all()
    ranked = 0
    for ev in rows:
        if not (ev.excerpt or "").strip() or ev.status in (EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value):
            continue
        out = ranker.score(claim, ev.excerpt, {"url": ev.url, "title": ev.title, "type": ev.type,
                                               "owned": ev.type == EvidenceType.OWNED.value, "status": ev.status})
        vals = out if isinstance(out, dict) else {k: getattr(out, k, None) for k in
                                                  ("support", "contradiction", "insufficient", "freshness_risk")}
        ev.support_score, ev.contradiction_score = vals.get("support"), vals.get("contradiction")
        ev.insufficient_score, ev.freshness_risk = vals.get("insufficient"), vals.get("freshness_risk")
        ev.raw = {**(ev.raw or {}), "ranker": {"available": bool(getattr(ranker, "available", True)),
                                               "stage": "pipeline.stage_rank"}}
        ranked += 1
    await session.commit()
    return {"ranked": ranked, "ranker": "ok" if getattr(ranker, "available", True) else "heuristic_fallback"}


def _rca_llm() -> Any | None:
    """Configured model client for hypothesis reasoning, or None (rules only, investigation still completes)."""
    from app.connectors.llm import get_llm_client

    return get_llm_client()


async def stage_hypotheses(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Rules first, optional LLM over the evidence. All hypotheses persisted as `proposed`."""
    from app.investigation.rca import RCAConfig, arun_rca

    inc = await _fresh(session, Incident, incident_id)
    evidence = (await session.execute(select(Evidence).where(Evidence.incident_id == incident_id))).scalars().all()
    llm = _rca_llm()
    cap = get_settings()
    result = await arun_rca(
        inc, evidence, llm if cap.max_model_calls > 0 else None,
        config=RCAConfig(llm_max_hypotheses=cap.max_hypotheses),
    )
    await session.execute(delete(Hypothesis).where(Hypothesis.incident_id == incident_id))
    # The measured Profound change is the symptom every actionable explanation refers to; cite it explicitly so the
    # evidence gate can check it (the "no actionable cause" fallback cites nothing and can never be confirmed).
    symptom_ids = [str(e.id) for e in evidence if str(e.type) == EvidenceType.PROFOUND.value]
    meta: dict[str, Any] = {}
    for d in result.hypotheses:
        cited = list(d.evidence_ids)
        if d.actionable:
            cited = list(dict.fromkeys([*cited, *symptom_ids]))
        row = Hypothesis(incident_id=incident_id, title=d.title, summary=d.summary,
                         status=HypothesisStatus.PROPOSED.value, confidence=d.confidence,
                         evidence_ids=cited, rationale=d.rationale, produced_by=d.produced_by)
        session.add(row)
        await session.flush()
        meta[str(row.id)] = {"rule_id": d.rule_id, "layer": str(d.layer.value), "actionable": d.actionable,
                             "missing_evidence": d.missing_evidence,
                             "contradicting_evidence_ids": d.contradicting_evidence_ids}
    inc.context = {**(inc.context or {}), "hypotheses_meta": meta,
                   "llm": {"used": result.llm_used, "warnings": result.warnings,
                           "available": llm is not None, "calls": result.llm_calls}}
    await session.commit()
    return {"hypotheses": len(result.hypotheses), "llm_used": result.llm_used, "llm_available": llm is not None,
            "warnings": result.warnings}


async def stage_graph(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Persist the investigative trail: root symptom -> prompt cluster -> evidence; evidence -> hypotheses
    (supports / contradicts). Edges carry provenance (source, timestamp, extract, hash, retrieval_method)."""
    from app.evidence.graph import build_graph

    inc = await _fresh(session, Incident, incident_id)
    await session.execute(delete(EvidenceEdge).where(EvidenceEdge.incident_id == incident_id))
    await session.execute(delete(EvidenceNode).where(EvidenceNode.incident_id == incident_id))
    evidence = list((await session.execute(select(Evidence).where(Evidence.incident_id == incident_id)
                                           .order_by(Evidence.created_at))).scalars())
    hyps = list((await session.execute(select(Hypothesis).where(Hypothesis.incident_id == incident_id))).scalars())
    hmeta = (inc.context or {}).get("hypotheses_meta", {})

    primary = inc.metrics[0] if inc.metrics else {}
    origin = await _metric_origin(session, inc)
    series_method = "profound.signal_series" if origin.from_profound else "detector"
    root = EvidenceNode(incident_id=incident_id, kind=NodeKind.ROOT.value, title=inc.title,
                        source=origin.label, ref_type="incidents", ref_id=str(inc.id),
                        retrieval_method=series_method,
                        observed_at=inc.first_observed_at, confidence=inc.confidence,
                        excerpt=inc.summary or None, content_hash=content_hash(inc.summary or inc.title),
                        data={"primary_metric": primary, "signal_source": origin.label})
    session.add(root)
    hub = root
    if inc.prompt_cluster_id:
        cluster = await session.get(PromptCluster, inc.prompt_cluster_id)
        hub = EvidenceNode(incident_id=incident_id, kind=NodeKind.PROMPT_CLUSTER.value,
                           title=f"Affected prompts: {cluster.topic if cluster else 'cluster'}",
                           source=origin.label, ref_type="prompt_clusters", ref_id=str(inc.prompt_cluster_id),
                           retrieval_method="profound.prompts" if origin.from_profound else "prompt_cluster",
                           observed_at=inc.first_observed_at,
                           data={"prompts": (cluster.prompts if cluster else [])[:25]})
        session.add(hub)
    await session.flush()

    def edge(src: uuid.UUID, dst: uuid.UUID, etype: EdgeType, *, source: str, extract: str | None,
             conf: float | None, method: str, ts: datetime | None = None, h: str | None = None,
             extract_kind: str = "excerpt") -> None:
        # provenance["hash"] always covers the stored EXTRACT; the source document's hash is kept as source_hash
        prov = edge_provenance(source=source, extract=extract, confidence=conf, retrieval_method=method, timestamp=ts)
        prov["extract_kind"] = extract_kind if extract else "none"
        if h:
            prov["source_hash"] = h
        session.add(EvidenceEdge(
            incident_id=incident_id, src_id=src, dst_id=dst, edge_type=etype.value, confidence=conf, provenance=prov))

    if hub is not root:
        edge(root.id, hub.id, EdgeType.ASSOCIATED_WITH, source=origin.label, extract=inc.summary or inc.title,
             conf=inc.confidence, method=series_method, ts=inc.first_observed_at)
    for ev in evidence:
        etype = str(ev.type)
        if etype == EvidenceType.PROFOUND.value:
            src, kind = root, EdgeType.TRIGGERED
        elif etype == EvidenceType.COMPETITOR.value:
            src, kind = hub, EdgeType.COMPETES_WITH
        else:
            src, kind = hub, EdgeType.ASSOCIATED_WITH
        extract, ekind = extract_for(ev)
        edge(src.id, ev.id, kind, source=ev.source or ev.url or "web", extract=extract,
             conf=ev.confidence, method=ev.retrieval_method or "unknown", ts=ev.observed_at or ev.retrieved_at,
             h=ev.content_hash, extract_kind=ekind)
    ev_by_id = {str(e.id): e for e in evidence}
    for h in hyps:
        contra = set((hmeta.get(str(h.id)) or {}).get("contradicting_evidence_ids", []))
        cited = False
        for eid in dict.fromkeys([*(h.evidence_ids or []), *contra]):
            ev = ev_by_id.get(str(eid))
            if ev is None:
                continue
            cited = True
            extract, ekind = extract_for(ev)
            symptom_only = (ev.raw or {}).get("kind") == "metric_change"
            if eid in contra:
                etype_, conf_ = EdgeType.CONTRADICTS, ev.contradiction_score
            elif symptom_only or ekind != "excerpt":
                # the measured symptom (or a title with no quoted text) says THAT something changed, not why: it is
                # associated with the hypothesis, never SUPPORTED_BY evidence it does not contain
                etype_, conf_ = EdgeType.ASSOCIATED_WITH, ev.confidence
            else:
                etype_ = EdgeType.SUPPORTS
                conf_ = ev.support_score if ev.support_score is not None else ev.confidence
            edge(ev.id, h.id, etype_, source=ev.source or ev.url or "evidence", extract=extract, conf=conf_,
                 method="rca", ts=ev.observed_at or ev.retrieved_at, h=ev.content_hash, extract_kind=ekind)
        if not cited:
            edge(root.id, h.id, EdgeType.ASSOCIATED_WITH, source="rca",
                 extract="This hypothesis cites no evidence.",
                 conf=h.confidence, method="rca", ts=inc.detected_at)
    await session.flush()
    nodes = list((await session.execute(select(EvidenceNode).where(EvidenceNode.incident_id == incident_id))).scalars())
    edges = list((await session.execute(select(EvidenceEdge).where(EvidenceEdge.incident_id == incident_id))).scalars())
    graph = build_graph(inc, evidence, edges, hyps, nodes)
    await session.commit()
    rep = graph.report
    return {"nodes": len(nodes) + len(evidence) + len(hyps), "edges": len(edges),
            "dangling": len(rep.dangling_edges), "cycles": len(rep.cycles)}


async def _evidence_fingerprint(session: AsyncSession, incident_id: uuid.UUID) -> str:
    """Stable digest of the evidence set (content hashes + statuses). Same digest twice = nothing new was learned."""
    rows = (await session.execute(select(Evidence.content_hash, Evidence.status, Evidence.type)
                                  .where(Evidence.incident_id == incident_id))).all()
    return content_hash("|".join(sorted(f"{t}:{s}:{h or ''}" for h, s, t in rows))) or ""


def counter_fetcher_factory() -> Any:
    """(fetcher, closer) for uncached re-verification of leading supporting pages. Overridable in tests."""
    from app.connectors.web import WebCollector

    collector = WebCollector()

    async def fetch(url: str) -> Any:
        return await collector.fetch(url, use_cache=False)

    return fetch, collector.aclose


async def stage_gate(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Two-stage gate. Stage 1: the deterministic evidence gate. Stage 2 (investigation.assessment): taxonomy cause,
    evidence-derived confidence, active counterevidence search, control-movement check, scope. A hypothesis becomes
    `confirmed` only if BOTH pass; no model output can confirm anything."""
    from app.investigation.assessment import assess_investigation
    from app.investigation.control import assess_incident_controls
    from app.investigation.counterevidence import reverify_urls, run_checks
    from app.investigation.evidence_gate import confirm_aeo_root_cause

    inc = await _fresh(session, Incident, incident_id)
    evidence = list((await session.execute(select(Evidence).where(Evidence.incident_id == incident_id))).scalars())
    hyps = list((await session.execute(select(Hypothesis).where(Hypothesis.incident_id == incident_id))).scalars())
    hmeta = (inc.context or {}).get("hypotheses_meta", {})
    candidates = sorted((h for h in hyps if (hmeta.get(str(h.id)) or {}).get("actionable", True)),
                        key=lambda h: -(h.confidence or 0.0))[:MAX_GATE_HYPOTHESES]
    results: dict[str, Any] = {}
    gate_objs: dict[str, Any] = {}
    for h in candidates:
        gate = confirm_aeo_root_cause(h, evidence, incident_at=inc.first_observed_at or inc.detected_at,
                                      category=inc.category)
        gate_objs[str(h.id)] = gate
        results[str(h.id)] = {"title": h.title, **gate.to_dict()}

    try:
        control = await assess_incident_controls(session, inc)
    except Exception:  # noqa: BLE001 - control data is optional; absence means "not established", never "clear"
        log.warning("pipeline.control_failed", incident=str(incident_id))
        control = None
    first = assess_investigation(inc, candidates, hmeta, evidence, gate_objs, control)
    overrides: dict[str, Any] = {}
    leader = first.assessments[0] if first.assessments else None
    if leader is not None and leader.gate_confirmed:
        web_rows = sum(1 for e in evidence if str(e.type) in ("owned", "competitor", "external"))
        allowed = min(2, max(0, get_settings().max_web_sources - web_rows))
        if allowed:
            fetcher = closer = None
            try:
                fetcher, closer = counter_fetcher_factory()
                rep = run_checks(leader.cause, evidence, control, set(gate_objs[leader.hypothesis_id].supporting_ids))
                overrides[leader.hypothesis_id] = await reverify_urls(
                    rep, evidence, set(gate_objs[leader.hypothesis_id].supporting_ids), fetcher, max_requests=allowed)
            except Exception:  # noqa: BLE001
                log.warning("pipeline.counter_reverify_failed", incident=str(incident_id))
            finally:
                if closer is not None:
                    await closer()
    assessment = assess_investigation(inc, candidates, hmeta, evidence, gate_objs, control,
                                      counter_overrides=overrides) if overrides else first
    best_gate, best_h = None, None
    for h in candidates:
        a = assessment.get(str(h.id))
        if a is None:
            continue
        results[str(h.id)]["assessment"] = a.to_dict()
        hmeta_h = dict(hmeta.get(str(h.id)) or {})
        hmeta_h.update({"cause": a.cause.value, "prior_confidence": h.confidence})
        hmeta[str(h.id)] = hmeta_h
        h.confidence = a.confidence.value  # evidence-derived; the rule/LLM prior is one weak feature of it
        if a.allow_confirm:
            h.status = HypothesisStatus.CONFIRMED.value
            if best_gate is None or a.confidence.value > assessment.get(str(best_h.id)).confidence.value:
                best_gate, best_h = gate_objs[str(h.id)], h
    ctx = {**(inc.context or {}), "gate": results, "hypotheses_meta": hmeta,
           "assessment": {**assessment.to_dict(), "ranking": [
               {k: v for k, v in r.items() if k != "counterevidence"} | {"counter_found": r["counterevidence"]["found"]}
               for r in assessment.to_dict()["ranking"]]},
           "scope": assessment.scope}
    if best_h is None and results:  # surface the strongest failed gate result for the UI
        top = max(results.items(), key=lambda kv: (kv[1].get("assessment") or {}).get("confidence", {}).get("value", 0.0))
        ctx["gate_primary"] = {"hypothesis_id": top[0], "confirmed": False}
    elif best_h is not None:
        ctx["gate_primary"] = {"hypothesis_id": str(best_h.id), "confirmed": True}
        inc.confidence = assessment.get(str(best_h.id)).confidence.value
    ctx.update(_features_from(inc, evidence, best_h, hmeta))
    inc.context = ctx
    await session.commit()
    return {"confirmed": best_h is not None, "primary_hypothesis_id": str(best_h.id) if best_h else None,
            "evaluated": len(results), "gate": best_gate.to_dict() if best_gate else None,
            "outcome": assessment.outcome, "control": control.verdict.value if control else "not_checked"}


def _features_from(inc: Incident, evidence: list[Evidence], top: Hypothesis | None,
                   hmeta: dict[str, Any]) -> dict[str, Any]:
    """Policy context features that can be read off the investigation (never invented: omitted when unknown)."""
    out: dict[str, Any] = {}
    owned = [e for e in evidence if e.type == EvidenceType.OWNED.value]
    live_owned = [e for e in owned if e.status in (EvidenceStatus.LIVE.value, EvidenceStatus.CHANGED.value,
                                                   EvidenceStatus.STALE.value) and (e.excerpt or "").strip()]
    if owned:
        out["content_exists"] = 1.0 if live_owned else 0.0
    layer = (hmeta.get(str(top.id)) or {}).get("layer") if top is not None else None
    if layer:
        out["owned_source"] = 1.0 if layer in ("owned_content", "canonical_truth") else 0.0
        out["third_party_source"] = 1.0 if layer in ("citation", "external_web") else 0.0
    # observed_at comes back tz-naive on SQLite (tz-aware on Postgres); normalise before subtracting
    ages = [(utcnow() - (e.observed_at if e.observed_at.tzinfo else e.observed_at.replace(tzinfo=UTC))).days
            for e in live_owned if e.observed_at]
    if ages:
        out["source_age_days"] = float(max(0, min(ages)))
    contradicted = [e for e in evidence if (e.contradiction_score or 0) >= 0.5 and e.type != "profound"]
    if str(inc.category) == "factual_conflict":
        out["factual_conflict"] = 1.0 if contradicted or top is not None else 0.5
    elif contradicted:
        out["factual_conflict"] = min(1.0, max(e.contradiction_score or 0 for e in contradicted))
    return out


async def investigate(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Full investigation: profound evidence -> web evidence -> rank -> hypotheses -> graph -> evidence gate.
    Any step may fail without stopping the others; the outcome is `complete` or `incomplete`.

    Single-flight: the incident row is locked while a lease is claimed, so two workers cannot investigate the same
    incident at once (the loser returns `already_running`). A crashed worker's lease expires on its own."""
    inc = await _fresh_locked(session, Incident, incident_id)
    if inc is None:
        raise PermanentError(f"incident {incident_id} not found")
    now = utcnow()
    lease = (inc.context or {}).get("investigation_lease_until")
    if inc.investigation_status == "running" and lease and datetime.fromisoformat(lease) > now:
        await session.rollback()
        return {"incident_id": str(incident_id), "status": "already_running"}
    until = now + timedelta(seconds=float(get_settings().max_investigation_seconds) * 1.5 + 60)
    inc.context = {**(inc.context or {}), "investigation_lease_until": until.isoformat()}
    inc.investigation_status = "running"
    await session.commit()
    try:
        return await _investigate(session, incident_id)
    except BaseException:
        await session.rollback()
        inc = await _fresh(session, Incident, incident_id)
        if inc is not None and inc.investigation_status == "running":
            inc.investigation_status = "incomplete"
            await session.commit()
        raise


async def _investigate(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    inc = await _fresh(session, Incident, incident_id)
    if inc is None:
        raise PermanentError(f"incident {incident_id} not found")
    state = S(inc.state)
    if state not in INVESTIGABLE:
        raise PermanentError(f"incident is {state.value}; investigation is only possible before an intervention "
                             "is proposed")
    if state == S.DETECTED:
        await _move(session, inc, S.TRIAGED, "queued for investigation")
        state = S.TRIAGED
    if state == S.TRIAGED:
        await _move(session, inc, S.INVESTIGATING, "investigation started")
    elif state in (S.EVIDENCE_READY, S.ROOT_CAUSE_PROPOSED):
        await _move(session, inc, S.INVESTIGATING, "re-investigation requested")
    inc.investigation_status = "running"
    await audit(session, "system", ACTOR, "incident", incident_id, "investigation.started", {"state": S(inc.state).value})
    await session.commit()
    await _emit(session, incident_id, "investigation.started", StepStatus.RUNNING, "Investigation started", {})

    steps: dict[str, Any] = {}
    started = utcnow()
    limit = get_settings().max_investigation_seconds
    previous_fp = (((inc.context or {}).get("investigation") or {}).get("evidence_fingerprint"))

    async def step(key: str, label: str, fn: Callable[[AsyncSession, uuid.UUID], Awaitable[dict[str, Any]]],
                   announce: bool = True) -> dict[str, Any] | None:
        if budget_exhausted(started, utcnow(), limit):
            steps[key] = {"status": "skipped", "reason": "investigation_budget_exhausted"}
            await _emit(session, incident_id, f"{key}.completed", StepStatus.WARNING,
                        f"{label}: stopped, investigation budget exhausted",
                        {"reason": "insufficient"})
            return None
        if announce:
            await _emit(session, incident_id, f"{key}.started", StepStatus.RUNNING, label, {})
        try:
            out = await fn(session, incident_id)
        except Exception as exc:
            await session.rollback()
            log.exception("pipeline.step_failed", step=key, incident=str(incident_id))
            steps[key] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            await _emit(session, incident_id, f"{key}.completed", StepStatus.FAILED, f"{label}: failed",
                        {"error": f"{type(exc).__name__}: {exc}"})
            return None
        steps[key] = {"status": "ok", **{k: v for k, v in out.items() if k != "gate"}}
        await _emit(session, incident_id, f"{key}.completed", StepStatus.SUCCESS, _summarise(key, out), out)
        return out

    inc = await _fresh(session, Incident, incident_id)
    origin = await _metric_origin(session, inc)
    metric_label = ("Recording measured Profound changes as evidence" if origin.from_profound
                    else f"Recording measured metric changes from {origin.label} (not a live Profound pull)")
    await step("evidence.profound", metric_label, stage_profound)
    await step("evidence.fanout", "Comparing query fanouts for a material interpretation change", stage_fanout)
    await step("evidence.answers", "Reading Profound answer rows for this incident", stage_answers)
    await step("evidence.web", "Collecting public web evidence", stage_collect, announce=False)
    await step("ranking", "Scoring evidence (support / contradiction / freshness)", stage_rank)
    await step("hypotheses", "Generating root-cause hypotheses from evidence", stage_hypotheses)
    await step("graph", "Building evidence graph", stage_graph)

    inc = await _fresh(session, Incident, incident_id)
    n_evidence = len((await session.execute(select(Evidence.id).where(Evidence.incident_id == incident_id))).all())
    failed = [k for k, v in steps.items() if v.get("status") == "failed"]
    skipped = [k for k, v in steps.items() if v.get("status") == "skipped"]
    inc.context = {**(inc.context or {}), "investigation": {
        "steps": steps, "failed_steps": failed, "skipped_steps": skipped,
        "budget_exhausted": bool(skipped), "finished_at": utcnow().isoformat(),
        "usage": {
            "web_pages": (steps.get("evidence.web") or {}).get("web_evidence", 0),
            "answer_rows": (steps.get("evidence.answers") or {}).get("answer_rows", 0),
            "answer_requests": (steps.get("evidence.answers") or {}).get("requests", 0),
            "model_calls": 1 if (steps.get("hypotheses") or {}).get("llm_used") else 0,
        },
    }}
    inc.investigation_status = "incomplete" if failed or skipped else "complete"
    await session.commit()
    tracker = BudgetTracker(InvestigationBudget.from_settings(get_settings()), started)
    usage = inc.context["investigation"]["usage"]
    tracker.web_requests, tracker.llm_calls, tracker.evidence = usage["web_pages"], usage["model_calls"], n_evidence
    if (skipped or tracker.time_up(utcnow())) and "seconds" not in tracker.exhausted:
        tracker.exhausted.append("seconds")
    if usage["web_pages"] >= tracker.budget.max_web_requests and "web_requests" not in tracker.exhausted:
        tracker.exhausted.append("web_requests")
    await _move(session, inc, S.EVIDENCE_READY, f"{n_evidence} evidence item(s) collected"
                + (f"; incomplete: {', '.join(failed)}" if failed else "")
                + ("; investigation budget exhausted" if skipped else ""))

    gate_out = None
    has_hyps = "hypotheses" not in failed
    if has_hyps:
        await _move(session, inc, S.ROOT_CAUSE_PROPOSED, "hypotheses proposed (not yet confirmed)")
        gate_out = await step("gate", "Running evidence gate on proposed root causes", stage_gate)
        inc = await _fresh(session, Incident, incident_id)
        if gate_out and gate_out.get("confirmed"):
            gate = _GateView(confirmed=True)
            await _move(session, inc, S.ROOT_CAUSE_CONFIRMED, "evidence gate passed", actor="evidence_gate",
                        gate=gate, meta={"hypothesis_id": gate_out["primary_hypothesis_id"]})
    else:
        await _emit(session, incident_id, "gate.skipped", StepStatus.WARNING,
                    "No hypotheses available; investigation incomplete. Recommend observe / human investigation.", {})
    inc = await _fresh(session, Incident, incident_id)
    stop = decide_stop(confirmed=bool(gate_out and gate_out.get("confirmed")), tracker=tracker,
                       evidence_fingerprint=await _evidence_fingerprint(session, incident_id),
                       previous_fingerprint=previous_fp)
    inv = dict((inc.context or {}).get("investigation") or {})
    inv.update({"stop_reason": stop.value, "budget": tracker.to_dict(),
                "evidence_fingerprint": await _evidence_fingerprint(session, incident_id)})
    inc.context = {**(inc.context or {}), "investigation": inv}
    await session.commit()
    await _emit(session, incident_id, "investigation.completed",
                StepStatus.WARNING if failed else StepStatus.SUCCESS,
                f"Investigation {inc.investigation_status}; state {inc.state}", {"steps": steps})
    await audit(session, "system", ACTOR, "incident", incident_id, "investigation.finished",
                {"status": inc.investigation_status, "failed_steps": failed,
                 "root_cause_confirmed": bool(gate_out and gate_out.get("confirmed"))}, commit=True)
    nxt = None
    if S(inc.state) in CONFIRMING_STATES and has_hyps:
        nxt = await _chain("score_interventions", {"incident_id": str(incident_id)},
                           lambda: propose(session, incident_id), session, incident_id)
    return {"incident_id": str(incident_id), "status": inc.investigation_status, "state": str(inc.state),
            "failed_steps": failed, "next": nxt}


class _GateView:
    def __init__(self, confirmed: bool):
        self.confirmed = confirmed


def _summarise(key: str, out: dict[str, Any]) -> str:
    if key == "evidence.profound":
        n = out.get("profound_evidence", 0)
        source = out.get("metric_source") or "unknown"
        if out.get("from_profound"):
            return f"{n} Profound metric change(s) recorded"
        return f"{n} measured metric change(s) recorded from {source} (not a live Profound pull)"
    if key == "evidence.answers":
        if out.get("reason") == "profound_not_configured":
            return "Profound answers not requested: no API key"
        return f"{out.get('answer_rows', 0)} Profound answer row(s) stored for this incident"
    if key == "evidence.fanout":
        n = out.get("fanout_shifts", 0)
        if out.get("reason") == "no_fanout_signals":
            return "No query-fanout signals for this incident"
        return f"{n} material query-fanout change(s) recorded" if n else "No material query-fanout change"
    if key == "evidence.web":
        return f"{out.get('web_evidence', 0)} public web page(s) collected ({out.get('by_status')})"
    if key == "ranking":
        return f"{out.get('ranked', 0)} evidence item(s) scored (ranker: {out.get('ranker')})"
    if key == "hypotheses":
        return f"{out.get('hypotheses', 0)} hypothesis(es) generated" + (
            " (rules + LLM)" if out.get("llm_used") else " (rules only)")
    if key == "graph":
        return f"Evidence graph: {out.get('nodes')} nodes, {out.get('edges')} edges"
    if key == "gate":
        return ("Evidence gate confirmed the root cause" if out.get("confirmed")
                else "Evidence gate did not confirm any root cause; hypotheses remain proposed")
    return key


# ---------------------------------------------------------------------------------------------------------
# propose interventions via the policy


async def propose(session: AsyncSession, incident_id: uuid.UUID) -> dict[str, Any]:
    """Policy selects an action, candidate interventions are built, the experiment is opened (status
    `proposed`, full before-state) and a human approval is requested. Nothing is executed."""
    from app.interventions import propose_interventions
    from app.models.policy import PolicyDecision
    from app.policy.bandit import Decision, resolve_allowed_actions
    from app.policy.context import build_policy_context
    from app.policy.store import PolicyStore
    from app.services import approvals

    inc = await _fresh(session, Incident, incident_id)
    if inc is None:
        raise PermanentError(f"incident {incident_id} not found")
    state = S(inc.state)
    if state not in CONFIRMING_STATES:
        existing = await _existing_selected(session, incident_id)
        if existing is not None:
            return {"incident_id": str(incident_id), "status": "already_proposed",
                    "intervention_id": str(existing.id)}
        raise PermanentError(f"incident is {state.value}; interventions are proposed after investigation")
    confirmed = state == S.ROOT_CAUSE_CONFIRMED
    await _emit(session, incident_id, "policy.started", StepStatus.RUNNING,
                "Computing intervention policy scores", {"root_cause_confirmed": confirmed})

    # Context from persisted state only (incident, priority components, evidence, hypotheses, signals, history).
    pctx = await build_policy_context(session, inc)
    ctx = pctx.vector.values
    mask = pctx.mask
    store = PolicyStore(session)
    policy = await store.load_policy()
    if get_settings().policy_seed is not None:
        import numpy as np

        policy._rng = np.random.default_rng(get_settings().policy_seed)
    operator_mask = await store.load_allowed_actions()
    allowed = resolve_allowed_actions(operator_mask) if operator_mask else None
    if allowed is not None:
        policy.config.allowed_actions = tuple(a.value for a in allowed)
    # Eligibility mask is applied BEFORE scoring: masked actions are never scored and never selectable.
    if confirmed:
        decision = policy.select(ctx, eligible=mask.eligible, masked=mask.masked)
        note = {}
    else:
        # Low root-cause confidence: scores stay visible but the only selectable action is `observe`
        # (rule fallback, probability 1.0 -> excluded from off-policy evaluation by its selection_basis).
        probs = policy.probabilities(ctx, mask.eligible)
        scores = [replace(sc, probability=float(probs.get(sc.action, 0.0))) for sc in policy.score(ctx, mask.eligible)]
        decision = Decision(
            action=ActionType.OBSERVE, probability=1.0, scores=scores,
            selection_basis=SelectionBasis.RULE_FALLBACK, cold_start=True, policy_version=policy.version,
            allowed_actions=policy.selectable(mask.eligible), algorithm=policy.algorithm, context=policy.x_of(ctx),
            note="root cause not confirmed by the evidence gate: observe / human investigation recommended",
            feature_schema=policy.feature_schema, masked_actions=policy._masked(mask.eligible, mask.masked),
            full_context=ctx)
        note = {"constrained_to_observe": True, "reason": decision.note}
    note = {**note, "policy_context": pctx.to_json()}
    pd: PolicyDecision = await store.record_decision(decision, policy, incident_id, note)
    await _emit(session, incident_id, "policy.completed", StepStatus.SUCCESS,
                f"Policy {policy.version} selected {decision.action.value} (p={decision.probability:.2f}, "
                f"{decision.selection_basis.value})",
                {"action": decision.action.value, "probability": decision.probability,
                 "policy_version": policy.version, "cold_start": decision.cold_start,
                 "basis": decision.selection_basis.value})

    interventions = await propose_interventions(session, inc, decision)
    selected = next((i for i in interventions if i.selected), None)
    if selected is None:
        raise RuntimeError("propose_interventions returned no selected intervention")
    if ActionType(selected.action) != ActionType.OBSERVE and not selected.proposed_change:
        # The policy picked an action for which no grounded change could be drafted: never ask a human to approve
        # something unexecutable. Fall back to the best executable candidate, recorded as a rule fallback (p=1.0).
        executable = [i for i in interventions if i.proposed_change and i.id != selected.id
                      and ActionType(i.action) in mask.eligible]  # a masked action is never a fallback either
        alt = max(executable, key=lambda i: i.score or 0.0, default=None)
        why = f"policy chose {ActionType(selected.action).value} but no executable change could be drafted"
        decision = Decision(
            action=ActionType(alt.action) if alt else ActionType.OBSERVE, probability=1.0, scores=decision.scores,
            selection_basis=SelectionBasis.RULE_FALLBACK, cold_start=True, policy_version=policy.version,
            allowed_actions=policy.selectable(mask.eligible), algorithm=policy.algorithm, context=policy.x_of(ctx),
            note=why, feature_schema=policy.feature_schema, full_context=ctx,
            masked_actions=policy._masked(mask.eligible, mask.masked))
        pd = await store.record_decision(decision, policy, incident_id, {"fallback_from": str(selected.action),
                                                                          "reason": why, "policy_context": pctx.to_json()})
        await _emit(session, incident_id, "policy.fallback", StepStatus.WARNING,
                    f"{why}; falling back to {decision.action.value}", {"from": str(selected.action)})
        interventions = await propose_interventions(session, inc, decision)
        selected = next(i for i in interventions if i.selected)
    await session.commit()

    exp = await _experiment_of(session, selected.id)
    exp_note = None
    if exp is None:
        exp = await _open_experiment(session, inc, selected, decision)
        if exp is None:
            exp_note = "no measured before-metrics available; experiment ledger entry not opened"
            await _emit(session, incident_id, "experiment", StepStatus.WARNING, exp_note, {})
    approval = await approvals.request_approval(session, selected, requested_by=ACTOR)
    await session.commit()
    await _guard_proposal(session, selected)

    inc = await _fresh(session, Incident, incident_id)
    if S(inc.state) == S.ROOT_CAUSE_PROPOSED:
        await _move(session, inc, S.INTERVENTION_PROPOSED, "unconfirmed root cause: observe recommended",
                    meta={"action": ActionType.OBSERVE.value})
    else:
        await _move(session, inc, S.INTERVENTION_PROPOSED,
                    f"policy selected {decision.action.value}", meta={"policy_decision_id": str(pd.id)})
    await _move(session, inc, S.AWAITING_APPROVAL, "human approval required before any execution",
                meta={"intervention_id": str(selected.id), "approval_id": str(approval.id)})
    await audit(session, "system", ACTOR, "intervention", selected.id, "intervention.proposed",
                {"action": decision.action.value, "experiment_id": str(exp.id) if exp else None,
                 "policy_version": policy.version, "policy_probability": decision.probability}, commit=True)
    await _emit(session, incident_id, "approval.pending", StepStatus.WAITING,
                f"Awaiting human approval: {selected.title}",
                {"intervention_id": str(selected.id), "experiment_id": str(exp.id) if exp else None,
                 "alternatives": len(interventions) - 1})
    return {"incident_id": str(incident_id), "intervention_id": str(selected.id),
            "action": decision.action.value, "experiment_id": str(exp.id) if exp else None,
            "policy_version": policy.version, "experiment_note": exp_note}


async def _guard_proposal(session: AsyncSession, iv: Any) -> None:
    """Change Guard runs on Profound Lift's own proposal when it is proposed (verdict shown before approval) and again at
    approval. Best effort here: a guard problem never fails the proposal (approval re-runs it and refuses on BLOCK)."""
    from app.changeguard.service import evaluate_intervention

    iv_id = iv.id
    try:
        await evaluate_intervention(session, iv)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.warning("change_guard.propose_check_failed", intervention_id=str(iv_id), error=type(exc).__name__)


async def _open_experiment(session: AsyncSession, inc: Incident, iv: Any, decision: Any, *,
                           override_reason: str | None = None, override_by: str | None = None) -> Any | None:
    """Open the ledger entry (status `proposed`) with the full before-state; None if nothing was measured."""
    from app.experiments import ledger
    from app.experiments.window import window_from_settings

    before = _numeric(await metric_snapshot(session, inc.org_id, inc.prompt_cluster_id))
    if not before:  # fall back to the values measured when the incident was detected
        before = {m["key"]: m["after"] for m in inc.metrics or [] if m.get("key") and m.get("after") is not None}
    if not before:
        return None
    evidence = list((await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalars())
    window = window_from_settings()
    ctx = _ctx_json(decision) if hasattr(decision, "context") else (getattr(decision, "context_vector", None) or [])
    return await ledger.open_experiment(session, iv, decision, inc, before, evidence_snapshot(evidence), ctx,
                                        reason=iv.rationale, window=window, override_reason=override_reason,
                                        override_by=override_by)


async def _supersede_other_experiments(session: AsyncSession, incident_id: uuid.UUID, keep: uuid.UUID) -> None:
    """A human chose a different candidate than the policy: close the policy's still-`proposed` ledger entry."""
    from app.experiments.status import transition as exp_transition

    rows = (await session.execute(select(_exp_model()).where(_exp_model().incident_id == incident_id,
                                                              _exp_model().id != keep,
                                                              _exp_model().status == ExperimentStatus.PROPOSED))
            ).scalars().all()
    for e in rows:
        exp_transition(e, ExperimentStatus.REJECTED, "human", "superseded: a different candidate was approved")


def _ctx_json(decision: Any) -> Any:
    ctx = getattr(decision, "full_context", None)
    if ctx is None:
        ctx = getattr(decision, "context", None)
    if ctx is None:
        return []
    from app.policy.features import FEATURE_NAMES, SCHEMA, coerce_context

    return {"schema": SCHEMA, "names": list(FEATURE_NAMES), "values": [float(x) for x in coerce_context(ctx)]}


async def _experiment_of(session: AsyncSession, intervention_id: uuid.UUID) -> Any | None:
    from app.models.interventions import Experiment

    return (await session.execute(select(Experiment).where(Experiment.intervention_id == intervention_id)
                                  .order_by(Experiment.created_at.desc()).limit(1))).scalars().first()


async def _existing_selected(session: AsyncSession, incident_id: uuid.UUID) -> Any | None:
    from app.models.interventions import Intervention

    return (await session.execute(select(Intervention).where(Intervention.incident_id == incident_id,
                                                             Intervention.selected.is_(True)))).scalars().first()


# ---------------------------------------------------------------------------------------------------------
# execute (after human approval)


async def execute(session: AsyncSession, intervention_id: uuid.UUID, *, executor: str | None = None,
                  dry_run: bool | None = None) -> dict[str, Any]:
    """Activate an APPROVED intervention's experiment with the chosen executor (default: manual).

    Manual (default, every action): issues the intervention package and stops at `awaiting_human_execution`
    (incident stays `approved`); a human then calls `record_executed`. `observe` has no human step: it starts
    observing immediately. GitHub is used only when `executor="github"` is requested AND configured; a GitHub
    dry-run (preview) is never verified or rewarded. The approval gate is re-checked in `run_execution`."""
    from app.experiments import ledger
    from app.interventions.executor import (
        ExecutionRefused,
        ExecutionStatus,
        normalize_executor_choice,
        select_executor,
    )
    from app.interventions.service import run_execution
    from app.models.interventions import Intervention
    from app.services import approvals

    iv = await session.get(Intervention, intervention_id, populate_existing=True)
    if iv is None:
        raise PermanentError(f"intervention {intervention_id} not found")
    action = ActionType(iv.action)
    try:
        chosen = select_executor(action, choice=executor, dry_run=dry_run)  # refuses unavailable explicit choices
    except ExecutionRefused as exc:
        raise PermanentError(f"execution refused: {exc}") from exc
    if not chosen.capability().available and not getattr(chosen, "dry_run", False):  # preview needs no creds
        raise PermanentError(f"executor {chosen.name!r} is not available")
    inc = await _fresh(session, Incident, iv.incident_id)
    iid = inc.id
    approval = await approvals.require_executable_approval(session, iv)  # raises ExecutionNotAuthorized
    exp = await _experiment_of(session, iv.id)
    if exp is None:  # human override of the policy's selection: record it as a manual-override experiment
        from app.models.policy import PolicyDecision

        pd = (await session.execute(select(PolicyDecision).where(PolicyDecision.incident_id == inc.id)
                                    .order_by(PolicyDecision.created_at.desc()).limit(1))).scalars().first()
        if pd is None:
            raise PermanentError("no policy decision recorded for this incident; refusing to execute untracked")
        # Override record: policy action (pd.selected_action) vs the executed action + reason + who. The outcome belongs
        # to the EXECUTED action only; the policy-selected action is never credited or penalised for it.
        exp = await _open_experiment(
            session, inc, iv, pd,
            override_reason=(getattr(approval, "note", None) or "human selected a different candidate than the policy"),
            override_by=getattr(approval, "decided_by", None) or ACTOR)
        if exp is None:
            raise PermanentError("no measured before-metrics; refusing to execute untracked")
        await _supersede_other_experiments(session, inc.id, exp.id)
        await session.commit()
        await _emit(session, iid, "experiment", StepStatus.WARNING,
                    "Human overrode the policy selection; recorded as a manual-override experiment (no propensity)",
                    {"experiment_id": str(exp.id)})
    exp_id = exp.id
    if ExperimentStatus(exp.status) in (ExperimentStatus.EXECUTED, ExperimentStatus.AWAITING_VERIFICATION,
                                        ExperimentStatus.VERIFIED, ExperimentStatus.REWARDED):
        return {"intervention_id": str(iv.id), "status": "already_executed", "experiment_id": str(exp_id)}

    state = S(inc.state)
    if state == S.AWAITING_APPROVAL:
        await _move(session, inc, S.APPROVED, "approval granted", actor=(approval.decided_by if approval else ACTOR))
        state = S.APPROVED
    if state not in (S.APPROVED, S.EXECUTING):
        raise PermanentError(f"incident is {state.value}; cannot execute")
    exp = await _fresh(session, _exp_model(), exp_id)
    if ExperimentStatus(exp.status) == ExperimentStatus.PROPOSED:
        try:
            await ledger.attach_approval(session, exp, approval)  # also requires the declared hypothesis + metrics
        except SpecError as exc:
            await session.rollback()
            raise PermanentError(f"experiment not activated: {exc}") from exc
    try:  # contamination: refuse to activate while another intervention is active on the same target / cluster
        await ledger.activation_check(session, exp)
    except (SpecError, ExperimentCollision) as exc:
        await session.rollback()
        await _emit(session, iid, "experiment.collision", StepStatus.WARNING, f"Activation refused: {exc}",
                    {"experiment_id": str(exp_id)})
        raise PermanentError(f"experiment not activated: {exc}") from exc
    await session.commit()

    manual_package = normalize_executor_choice(chosen.name) == "manual" and action != ActionType.OBSERVE
    if manual_package:  # hand a human the exact change; nothing external happens
        from app.interventions.manual import prepare_manual_package

        try:
            row = await prepare_manual_package(session, intervention_id)  # idempotent
        except ExecutionRefused as exc:
            await session.rollback()
            raise PermanentError(f"execution refused: {exc}") from exc
        await session.commit()
        await audit(session, "system", ACTOR, "intervention", iv.id, "execution.package_ready",
                    {"execution_id": str(row.id), "executor": row.executor, "experiment_id": str(exp_id)}, commit=True)
        await _emit(session, iid, "execution.package_ready", StepStatus.WAITING,
                    f"Intervention package ready: a human applies {action.value} and marks it executed",
                    {"intervention_id": str(iv.id), "experiment_id": str(exp_id), "execution_id": str(row.id)})
        return {"intervention_id": str(iv.id), "experiment_id": str(exp_id), "status": "awaiting_human_execution",
                "execution_id": str(row.id), "executor": row.executor}

    if state == S.APPROVED:
        await _move(session, inc, S.EXECUTING, "execution started")
    await _emit(session, iid, "execution.started", StepStatus.RUNNING, f"Executing {action.value} via {chosen.name}",
                {"intervention_id": str(iv.id)})

    try:
        result, row = await run_execution(session, intervention_id, executor=chosen)
    except ExecutionRefused as exc:
        await session.rollback()
        raise PermanentError(f"execution refused: {exc}") from exc
    await session.commit()
    exp = await _fresh(session, _exp_model(), exp_id)
    inc = await _fresh(session, Incident, iid)

    if result.status is ExecutionStatus.FAILED:
        if result.retryable:
            await _emit(session, iid, "execution.completed", StepStatus.WARNING,
                        f"Execution failed (retryable): {result.error}", {"error_code": result.error_code})
            raise RuntimeError(f"retryable execution failure: {result.error}")
        await _move(session, inc, S.FAILED, f"execution failed: {result.error}")
        await _emit(session, iid, "execution.completed", StepStatus.FAILED, f"Execution failed: {result.error}",
                    {"error_code": result.error_code})
        return {"intervention_id": str(intervention_id), "status": "failed", "error": result.error}

    await session.commit()
    observing = action == ActionType.OBSERVE
    await _move(session, inc, S.EXECUTED, "executed" + (" (GitHub dry-run preview: nothing changed)" if exp.dry_run else ""),
                meta={"dry_run": exp.dry_run, "reference": row.reference, "executor": row.executor})
    if not exp.dry_run:
        inc = await _fresh(session, Incident, iid)
        await _move(session, inc, S.AWAITING_VERIFICATION, "waiting for post-intervention observation",
                    meta={"window_start": exp.verification_window_start.isoformat()
                          if exp.verification_window_start else None})
    await audit(session, "system", ACTOR, "experiment", exp_id, "experiment.executed",
                {"dry_run": exp.dry_run, "reference": row.reference, "executor": row.executor}, commit=True)
    await _emit(session, iid, "execution.completed",
                StepStatus.WARNING if exp.dry_run else StepStatus.SUCCESS,
                ("GitHub dry-run preview only: no change was made, experiment will not be verified"
                 if exp.dry_run else ("Observing: no change is made; measurement window started" if observing
                                      else f"Executed via {row.executor}"
                                      + (f": {row.reference}" if row.reference else ""))),
                {"experiment_id": str(exp_id), "dry_run": exp.dry_run, "reference": row.reference})
    if not exp.dry_run:
        await _emit(session, iid, "verification.waiting", StepStatus.WAITING,
                    "Awaiting post-intervention Profound observations (awaiting_reward)",
                    {"window_start": exp.verification_window_start.isoformat()
                     if exp.verification_window_start else None})
    return {"intervention_id": str(iv.id), "experiment_id": str(exp_id), "status": "executed",
            "dry_run": exp.dry_run, "reference": row.reference, "executor": row.executor}


async def record_executed(
    session: AsyncSession, intervention_id: uuid.UUID, executed_by: str, *, executed_at: datetime | None = None,
    reference_url: str | None = None, note: str | None = None, actual_change: str | None = None,
    actor_type: str = "human",
) -> dict[str, Any]:
    """A human reports that they applied the manual package: execution succeeded (real, not dry-run), verification
    window starts at `executed_at`, incident -> awaiting_verification. Raises the typed errors of
    `record_manual_execution` (nothing is persisted on refusal)."""
    from app.interventions.manual import record_manual_execution

    try:
        out = await record_manual_execution(
            session, intervention_id, executed_by, executed_at, reference_url, note, actual_change,
            actor_type=actor_type)
    except Exception:
        await session.rollback()
        raise
    iid, exp_id, row_id = out.incident.id, out.experiment.id, out.execution.id
    win = out.experiment.verification_window_start
    meta = {"experiment_id": str(exp_id), "execution_id": str(row_id), "executor": "manual",
            "executed_at": out.executed_at.isoformat(), "reference": out.execution.reference,
            "deviation": out.deviation, "dry_run": False,
            "window_start": win.isoformat() if win else None}
    await session.commit()
    for rec in out.transitions:
        await _emit(session, iid, "state", StepStatus.SUCCESS, f"{rec['from_state']} -> {rec['to_state']}",
                    {"from": rec["from_state"], "to": rec["to_state"], "reason": rec["reason"]})
    await _emit(session, iid, "execution.completed", StepStatus.SUCCESS,
                f"Executed manually by {executed_by}" + (" (deviation from proposal recorded)" if out.deviation else ""),
                meta)
    await _emit(session, iid, "verification.waiting", StepStatus.WAITING,
                "Awaiting post-intervention Profound observations (awaiting_reward)", meta)
    return {"intervention_id": str(intervention_id), "experiment_id": str(exp_id), "execution_id": str(row_id),
            "status": "executed", "dry_run": False, "executor": "manual", "deviation": out.deviation,
            "reference": meta["reference"], "executed_at": meta["executed_at"], "window_start": meta["window_start"]}


# ---------------------------------------------------------------------------------------------------------
# verification -> reward -> policy


async def due_experiment_ids(session: AsyncSession) -> list[uuid.UUID]:
    from app.experiments import window as vwindow
    from app.models.interventions import Experiment

    now = vwindow.now()
    rows = (await session.execute(
        select(Experiment.id).where(
            Experiment.status == ExperimentStatus.AWAITING_VERIFICATION, Experiment.dry_run.is_(False),
            Experiment.verification_window_start <= now)
    )).scalars().all()
    return list(rows)


async def verify(session: AsyncSession, experiment_id: uuid.UUID, *, force: bool = False) -> dict[str, Any]:
    """Measure the outcome. Only Profound signals observed after the verification window opens (and after
    execution) count. No such data -> the experiment stays `awaiting_verification` (awaiting_reward)."""
    from app.experiments import window as vwindow
    from app.experiments.verification import NotEligible, apply_measurement

    exp = await _fresh_locked(session, _exp_model(), experiment_id)  # two verify jobs serialize on this row
    if exp is None:
        raise PermanentError(f"experiment {experiment_id} not found")
    status = ExperimentStatus(exp.status)
    inc = await _fresh(session, Incident, exp.incident_id)
    iid = inc.id
    if exp.dry_run:
        return {"experiment_id": str(experiment_id), "status": "not_verifiable",
                "reason": "dry-run execution changed nothing"}
    if status == ExperimentStatus.REWARDED:
        return {"experiment_id": str(experiment_id), "status": "already_rewarded"}
    if status not in (ExperimentStatus.AWAITING_VERIFICATION, ExperimentStatus.VERIFIED):
        raise PermanentError(f"experiment is {status.value}; nothing to verify")

    ingest_org = _optional("app.services.ingestion", "ingest_org")
    if ingest_org is not None and status == ExperimentStatus.AWAITING_VERIFICATION:
        try:  # refresh signals first; Profound being unavailable must not block verification of existing data
            await ingest_org(session, inc.org_id)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            await _emit(session, iid, "verification.ingest", StepStatus.WARNING,
                        f"Could not refresh Profound signals: {type(exc).__name__}", {})
            exp = await _fresh(session, _exp_model(), experiment_id)

    exp = await _fresh_locked(session, _exp_model(), experiment_id)  # the ingest commit above released the lock
    status = ExperimentStatus(exp.status)
    # The window rules live in ONE service (app.experiments.window) with an injected clock; nothing here re-derives them.
    from app.measurement.validation_snapshot import effective_verification_start, ensure_validation_snapshot

    now = vwindow.now()
    start = effective_verification_start(exp, inc) if inc else (
        vwindow.aware(exp.verification_window_start) if exp.verification_window_start else None
    )
    # `force` never relaxes the Profound-lag window: an observation taken before the window opens is not an outcome
    # (it is accepted only to re-run verification immediately once the window is open).
    gate = vwindow.attempt_allowed(start, now, dry_run=bool(exp.dry_run))
    if status == ExperimentStatus.AWAITING_VERIFICATION and not gate.ok:
        shown = start.isoformat() if start else None
        await _emit(session, iid, "verification.waiting", StepStatus.WAITING,
                    f"Verification window opens {shown}", {"window_start": shown})
        return {"experiment_id": str(experiment_id), "status": "awaiting_window", "window_start": shown}

    executed = vwindow.aware(exp.executed_at)
    after_from = max(executed, start) if start else executed
    # end=now: a measurement stamped in the future is never an outcome
    snap = await metric_snapshot(session, inc.org_id, inc.prompt_cluster_id, start=after_from, end=now)
    if not _numeric(snap) and gate.ok:
        wrote = await ensure_validation_snapshot(session, exp, inc, observed_at=now)
        if wrote:
            await session.commit()
            exp = await _fresh_locked(session, _exp_model(), experiment_id)
            inc = await _fresh(session, Incident, iid)
            snap = await metric_snapshot(session, inc.org_id, inc.prompt_cluster_id, start=after_from, end=now)
    metrics = _numeric(snap)
    if not metrics:
        await _emit(session, iid, "verification.waiting", StepStatus.WAITING,
                    "No post-intervention Profound observations yet (awaiting_reward)",
                    {"since": after_from.isoformat()})
        return {"experiment_id": str(experiment_id), "status": "awaiting_observation",
                "since": after_from.isoformat()}
    observed_at = datetime.fromisoformat(snap["_observed_at"])  # the SOURCE's timestamp, not the request time
    if status == ExperimentStatus.AWAITING_VERIFICATION:
        import hashlib

        run_id = hashlib.sha256(",".join(sorted(snap.get("_signal_ids", []))).encode()).hexdigest()[:32]
        try:
            await apply_measurement(
                session, exp, metrics, ",".join(snap.get("_sources") or ["profound"]), observed_at, run_id=run_id,
                extra={"_signal_ids": snap.get("_signal_ids", []), "_forced": bool(force)}, window_start=start)
        except NotEligible as exc:
            await session.rollback()
            await _emit(session, iid, "verification.waiting", StepStatus.WAITING,
                        f"Measurement not eligible: {exc.eligibility.reason}", {"code": exc.eligibility.code})
            return {"experiment_id": str(experiment_id), "status": "awaiting_observation",
                    "reason": exc.eligibility.code}
        await session.commit()
        inc = await _fresh(session, Incident, iid)
        if S(inc.state) == S.AWAITING_VERIFICATION:
            await _move(session, inc, S.VERIFIED, "measured post-intervention observation recorded",
                        meta={"experiment_id": str(experiment_id)})
        await _emit(session, iid, "verification.completed", StepStatus.SUCCESS,
                    f"Post-intervention metrics measured: {', '.join(f'{k}={v:g}' for k, v in metrics.items())}",
                    {"experiment_id": str(experiment_id), "after_metrics": metrics})
    nxt = await _chain("calculate_reward", {"experiment_id": str(experiment_id)},
                       lambda: reward(session, experiment_id), session, iid)
    return {"experiment_id": str(experiment_id), "status": "verified", "after_metrics": metrics, "next": nxt}


def _exp_model() -> type:
    from app.models.interventions import Experiment

    return Experiment


async def reward(session: AsyncSession, experiment_id: uuid.UUID) -> dict[str, Any]:
    """Measured reward -> Reward row -> NEW immutable PolicyVersion (one atomic, idempotent ingest)."""
    from app.learning.ingest import AlreadyRewarded, NotRewardable, ingest_reward
    from app.learning.reward import NoObservation

    exp = await _fresh_locked(session, _exp_model(), experiment_id)  # exactly-once: serialize on the experiment row
    if exp is None:
        raise PermanentError(f"experiment {experiment_id} not found")
    iid = exp.incident_id
    try:
        res = await ingest_reward(session, experiment_id)
    except AlreadyRewarded:
        return {"experiment_id": str(experiment_id), "status": "already_rewarded"}
    except NoObservation as exc:
        await session.rollback()
        await _emit(session, iid, "reward.waiting", StepStatus.WAITING, f"No measured outcome yet: {exc}", {})
        return {"experiment_id": str(experiment_id), "status": "awaiting_observation", "reason": str(exc)}
    except NotRewardable as exc:
        await session.rollback()
        raise PermanentError(str(exc)) from exc
    await session.commit()
    if not res.learned:  # INCONCLUSIVE: outcome recorded, nothing to learn from, no reward row, no policy update
        why = (res.outcome.methodology or {}).get("reason", "inconclusive")
        await _emit(session, iid, "reward.inconclusive", StepStatus.WARNING,
                    f"Outcome inconclusive ({why}); no reward and no policy update",
                    {"experiment_id": str(experiment_id), "confounders": res.outcome.confounders})
        return {"experiment_id": str(experiment_id), "status": "inconclusive", "reason": why}
    inc = await _fresh(session, Incident, iid)
    if S(inc.state) == S.VERIFIED:
        await _move(session, inc, S.REWARDED, "reward computed from measured outcome",
                    meta={"experiment_id": str(experiment_id), "reward": res.reward.total})
    await _emit(session, iid, "reward.completed", StepStatus.SUCCESS,
                f"Reward {res.reward.total:+.3f} computed from measured outcome",
                {"experiment_id": str(experiment_id), "total": res.reward.total, "outcome": res.outcome.outcome.value,
                 "causal_confidence": res.outcome.causal_confidence, "confounders": res.outcome.confounders,
                 "components": res.outcome.components(), "weights": res.reward.weights})
    await _emit(session, iid, "policy.updated", StepStatus.SUCCESS,
                f"Policy updated {res.parent_version.version} -> {res.policy_version.version}",
                {"from": res.parent_version.version, "to": res.policy_version.version,
                 "experiment_id": str(experiment_id)})
    return {"experiment_id": str(experiment_id), "status": "rewarded", "reward": res.reward.total,
            "policy_version": res.policy_version.version, "parent_version": res.parent_version.version}


async def update_policy(session: AsyncSession, experiment_id: uuid.UUID) -> dict[str, Any]:
    """Confirm (or perform, if the reward step has not yet run) the versioned policy update for an experiment.
    The update itself is applied exactly once, inside `reward`."""
    from app.models.policy import PolicyVersion

    pv = (await session.execute(select(PolicyVersion).where(PolicyVersion.source_experiment_id == experiment_id)
                                )).scalars().first()
    if pv is None:
        out = await reward(session, experiment_id)
        if out.get("status") != "rewarded":
            return out
        pv = (await session.execute(select(PolicyVersion).where(
            PolicyVersion.source_experiment_id == experiment_id))).scalars().first()
    return {"experiment_id": str(experiment_id), "status": "policy_updated", "policy_version": pv.version,
            "n_updates": pv.n_updates}
