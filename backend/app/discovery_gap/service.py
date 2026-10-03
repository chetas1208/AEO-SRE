"""Run the discovery-gap check for one organization and persist gaps as FACTUAL_CONFLICT incidents.

Honesty rules (enforced here, tested): no canonical truth => `skipped_no_canonical_truth`, nothing created, no Profound
call made; FactCheck unavailable => `skipped_factcheck_unavailable` + exact reason (answer-text fallback is labelled
lower confidence and only used when its endpoint answered); perceived claims come only from real Profound responses;
no automatic remediation and no hypotheses are invented (RCA rules run later on evidence like any other incident).
Flushes only: the caller commits (job / dev command).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.profound.errors import ProfoundError
from app.core.audit import audit
from app.discovery_gap import analyzer as an
from app.discovery_gap import perception as pc
from app.domain.enums import EvidenceStatus, EvidenceType, IncidentCategory, IncidentState
from app.evidence.provenance import content_hash
from app.incidents.priority import (
    compute_priority,
    feasibility_for_category,
    is_below_action_threshold,
    severity_from_priority,
)
from app.incidents.signature import build_signature
from app.models.changeguard import CanonicalClaim
from app.models.core import Incident, Organization
from app.models.evidence import Evidence

log = structlog.get_logger()
ACTOR = "discovery-gap"
RETRIEVAL = {pc.ORIGIN_FACTCHECK: "profound.factcheck_claims", pc.ORIGIN_ANSWER: "profound.answers"}
TERMINAL = {IncidentState.CLOSED.value, IncidentState.DISMISSED.value, IncidentState.FAILED.value,
            IncidentState.REWARDED.value, IncidentState.VERIFIED.value}
SEVERITY_BY_KIND = {"conflict": 0.85, "uncertain": 0.5}


@dataclass
class GapRunReport:
    status: str  # ok | skipped_no_canonical_truth | skipped_factcheck_unavailable | skipped_no_profound_category
    # | skipped_profound_not_configured | skipped_rate_limited | failed
    org_id: str
    dry_run: bool = False
    reason: str | None = None
    source_mode: str = "LIVE"
    category_id: str | None = None
    perception: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    suppressed_dismissed: list[str] = field(default_factory=list)
    canonical_claims_active: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _canonical_rows(rows: list[CanonicalClaim]) -> list[dict[str, Any]]:
    return [{"id": str(r.id), "key": r.key, "statement": r.statement, "status": r.status, "scope": r.scope,
             "entities": list(r.entities or []), "valid_from": r.valid_from, "valid_until": r.valid_until} for r in rows]


async def _category(session: AsyncSession, client: Any, org: Organization, category_id: str | None) -> tuple[str | None, int, str | None]:
    """(category_id, requests, reason). Explicit id wins; else the cached/ingested mapping; else domain match."""
    if category_id:
        return category_id, 0, None
    from app.connectors.profound.normalize import domain_of
    from app.services.ingestion import _get_setting, _resolve_category

    cached = await _get_setting(session, f"profound.category.{org.id}")
    if cached and cached.get("id"):
        return str(cached["id"]), 0, None
    try:
        found = await _resolve_category(client, domain_of(org.domain), None)
    except ProfoundError as exc:
        return None, 1, pc._unavailable_reason(exc)
    if found is None:
        return None, 1, f"no_profound_category_owns_{domain_of(org.domain)}"
    return found[0]["id"], 1, None


def _brand_terms(org: Organization) -> list[str]:
    from app.connectors.profound.normalize import domain_of

    terms = {(org.name or "").strip().lower(), domain_of(org.domain).split(".")[0]}
    return sorted(t for t in terms if len(t) >= 3)


async def run_discovery_gap(session: AsyncSession, org_id: uuid.UUID, *, client: Any = None, gateway: Any = None,
                            ranker: Any = None, dry_run: bool = False, now: datetime | None = None,
                            category_id: str | None = None, source_mode: str = "LIVE",
                            answers_fallback: bool = True, use_default_semantics: bool = True) -> GapRunReport:
    """`source_mode` is LIVE only when `client` is the real ProfoundClient; tests must pass SIMULATED/TEST."""
    now = now or datetime.now(UTC)
    org = await session.get(Organization, org_id)
    if org is None:
        raise ValueError(f"organization {org_id} not found")
    rep = GapRunReport(status="ok", org_id=str(org_id), dry_run=dry_run, source_mode=source_mode)

    rows = (await session.execute(select(CanonicalClaim).where(CanonicalClaim.org_id == org_id,
                                                               CanonicalClaim.status == "active"))).scalars().all()
    canon = _canonical_rows(list(rows))
    rep.canonical_claims_active = len(canon)
    usable, ignored = an.check_canonical_available(canon, now)
    if not usable:
        rep.status, rep.analysis = "skipped_no_canonical_truth", {"ignored_canonical": ignored}
        rep.reason = ("this organization has no active, currently valid canonical claims; nothing was compared and no "
                      "incident was created (canonical truth is never invented)")
        return rep

    owns = client is None
    if client is None:
        from app.connectors.profound import ProfoundClient

        client = ProfoundClient()
    try:
        cat, spent, why = await _category(session, client, org, category_id)
        if cat is None:
            rep.status = "skipped_profound_not_configured" if "NotConfigured" in (why or "") else "skipped_no_profound_category"
            rep.reason = why
            rep.perception = {"requests_made": spent}
            return rep
        rep.category_id = cat
        per = await pc.fetch_perception(client, cat, brand_terms=_brand_terms(org), now=now,
                                        answers_fallback=answers_fallback, source_mode=source_mode)
        per.requests_made += spent
    finally:
        if owns:
            await client.aclose()
    rep.perception = per.as_dict()
    if per.fatal == "profound_not_configured":
        rep.status, rep.reason = "skipped_profound_not_configured", per.factcheck_reason
        return rep
    if per.fatal == "rate_limited":
        rep.status, rep.reason = "skipped_rate_limited", per.factcheck_reason
        return rep
    if not per.claims:
        if per.factcheck_state == "unavailable":
            rep.status = "skipped_factcheck_unavailable"
            rep.reason = per.factcheck_reason + (f"; answer-text fallback: {per.answers_state}"
                                                   + (f" ({per.answers_reason})" if per.answers_reason else ""))
        else:  # reachable but nothing perceived: a real, empty observation, not a pass on correctness
            rep.status = "ok"
            rep.reason = ("no AI-perceived claims were returned (" + (per.factcheck_reason or "") +
                          ("; answers: " + per.answers_reason if per.answers_reason else "") + "); nothing to compare")
        return rep
    if per.factcheck_state == "unavailable":
        rep.reason = f"FactCheck unavailable ({per.factcheck_reason}); using answer-text claims at lower confidence"
    elif per.source == pc.ORIGIN_ANSWER:
        rep.reason = f"FactCheck returned no claims ({per.factcheck_reason}); using answer-text claims at lower confidence"

    gw = gateway if gateway is not None or not use_default_semantics else an.default_gateway()
    rk = ranker if ranker is not None or not use_default_semantics else an.default_ranker()
    result = await asyncio.to_thread(an.analyze, org_id, per.claims, canon, gateway=gw, ranker=rk, now=now)
    rep.analysis = result.as_dict()
    rep.gaps = [g.as_dict() for g in result.gaps]
    if dry_run:
        return rep
    await _persist(session, org, result.gaps, rep, now)
    return rep


# --------------------------------------------------------------------------- persistence
async def _existing(session: AsyncSession, org_id: uuid.UUID) -> dict[str, list[Incident]]:
    rows = (await session.execute(select(Incident).where(
        Incident.org_id == org_id, Incident.category == IncidentCategory.FACTUAL_CONFLICT.value))).scalars().all()
    out: dict[str, list[Incident]] = {}
    for r in rows:
        k = (r.context or {}).get("discovery_gap", {}).get("gap_key")
        if k:
            out.setdefault(k, []).append(r)
    return out


def _title(g: an.DiscoveryGap) -> str:
    return f"DISCOVERY GAP: we state \"{g.canonical_statement}\" but AI engines say \"{g.perceived_claim}\""[:512]


def _summary(g: an.DiscoveryGap) -> str:
    eng = ", ".join(g.engines) or "unnamed engines"
    cites = ", ".join(c["domain"] for c in g.citation_domains[:5]) or "none reported"
    return (f"Canonical claim {g.canonical_key!r}: \"{g.canonical_statement}\". Profound ({g.origin}, {g.source_mode}) "
            f"reports engines saying: \"{g.perceived_claim}\" (occurrence {g.occurrence} across {eng}). Relation "
            f"{g.relation} ({g.kind}, decided by {g.decided_by}, confidence {g.confidence}; semantic check "
            f"{g.semantic_check}). Cited sources: {cites}. Evidence grade: {g.evidence_grade}.")


def _gap_ctx(g: an.DiscoveryGap, now: datetime) -> dict[str, Any]:
    d = g.as_dict()
    d["checked_at"] = now.isoformat()
    return d


_MATERIAL = ("occurrence", "engines", "citation_domains", "kind", "confidence", "semantic_check", "last_observed_at")


def _evidence_raw(g: an.DiscoveryGap) -> dict[str, Any]:
    return {"kind": "discovery_gap_perception", "gap_key": g.gap_key, "source_mode": g.source_mode,
            "origin": g.origin, "evidence_grade": g.evidence_grade,
            "canonical_claim": {"id": g.canonical_claim_id, "key": g.canonical_key, "statement": g.canonical_statement},
            "perceived_claim": g.perceived_claim, "relation": g.relation, "relation_kind": g.kind,
            "decided_by": g.decided_by, "reasons": g.reasons, "engines": g.engines, "platforms": g.engines,
            "prompts": g.prompts, "occurrence": g.occurrence, "observations": g.observations,
            "citation_sources": g.citation_domains, "citation_urls": g.citation_urls, "window": g.window,
            "profound_reasoning": g.reasoning, "profound_evidence": g.perceived_evidence,
            "semantic_check": g.semantic_check, "score_basis": f"claim_compare:{g.decided_by}"}


async def _upsert_evidence(session: AsyncSession, inc: Incident, g: an.DiscoveryGap, now: datetime) -> bool:
    h = content_hash(f"discovery_gap|{g.gap_key}|{g.origin}")
    row = (await session.execute(select(Evidence).where(Evidence.incident_id == inc.id,
                                                        Evidence.content_hash == h))).scalars().first()
    fields = dict(
        type=EvidenceType.PROFOUND.value, status=EvidenceStatus.LIVE.value,
        title=f"AI engines say: {g.perceived_claim}"[:512], source=f"profound:{g.origin}"[:512],
        retrieval_method=RETRIEVAL[g.origin], retrieved_at=now, observed_at=g.last_observed_at or now,
        excerpt=g.perceived_claim, confidence=g.confidence,
        contradiction_score=g.confidence if g.kind == "conflict" else None, raw=_evidence_raw(g))
    if row is None:
        session.add(Evidence(incident_id=inc.id, content_hash=h, **fields))
        await session.flush()
        return True
    changed = row.raw != fields["raw"]
    for k, v in fields.items():
        if k not in ("retrieved_at",):
            setattr(row, k, v)
    await session.flush()
    return changed


async def _persist(session: AsyncSession, org: Organization, gaps: list[an.DiscoveryGap], rep: GapRunReport,
                   now: datetime) -> None:
    existing = await _existing(session, org.id)
    for g in gaps:
        live = [i for i in existing.get(g.gap_key, []) if i.state not in TERMINAL]
        if live:
            inc = live[0]
            old = (inc.context or {}).get("discovery_gap", {})
            new = _gap_ctx(g, now)
            changed = any(old.get(k) != new.get(k) for k in _MATERIAL)
            if changed:
                inc.context = {**(inc.context or {}), "discovery_gap": new}
                inc.confidence = g.confidence
                inc.summary = _summary(g)
                await audit(session, "system", ACTOR, "incident", inc.id, "discovery_gap.refreshed",
                            {"gap_key": g.gap_key, "occurrence": g.occurrence, "source_mode": g.source_mode})
            ev_changed = await _upsert_evidence(session, inc, g, now)
            (rep.updated if changed or ev_changed else rep.unchanged).append(str(inc.id))
            continue
        if any(i.state == IncidentState.DISMISSED.value for i in existing.get(g.gap_key, [])):
            rep.suppressed_dismissed.append(g.gap_key)
            continue
        pr = compute_priority(
            prompt_demand=None, buyer_intent=None, incident_severity=SEVERITY_BY_KIND[g.kind], persona_importance=None,
            competitive_displacement=None, evidence_confidence=g.confidence,
            remediation_feasibility=feasibility_for_category(IncidentCategory.FACTUAL_CONFLICT))
        sig = build_signature(
            incident_type="discovery_gap", family="discovery_gap", topic=g.canonical_key or g.canonical_claim_id,
            cluster_id=None, platform=g.engines[0] if len(g.engines) == 1 else None, persona=None, competitors=[],
            primary_metric="discovery_gap", direction="conflict", metrics=[], signal_families=["accuracy_drop"])
        sig["sources"] = [g.gap_key]  # one incident per (canonical claim, perceived claim), not per topic
        inc = Incident(
            org_id=org.id, title=_title(g), category=IncidentCategory.FACTUAL_CONFLICT.value,
            severity=severity_from_priority(pr.score).value, priority=pr.score, priority_breakdown=pr.to_dict(),
            state=IncidentState.DETECTED.value, detected_at=now, first_observed_at=g.first_observed_at or now,
            metrics=[], confidence=g.confidence, summary=_summary(g),
            context={"signature": sig, "family": "discovery_gap", "dedup_key": f"discovery_gap:{g.gap_key}",
                     "topic": g.canonical_key, "below_action_threshold": is_below_action_threshold(pr.score),
                     "discovery_gap": _gap_ctx(g, now)})
        try:
            async with session.begin_nested():
                session.add(inc)
                await session.flush()
        except IntegrityError:  # fingerprint backstop: a concurrent run created it first
            rep.unchanged.append(g.gap_key)
            continue
        await _upsert_evidence(session, inc, g, now)
        await audit(session, "system", ACTOR, "incident", inc.id, "incident.detected",
                    {"title": inc.title, "category": inc.category, "priority": inc.priority, "gap_key": g.gap_key,
                     "source_mode": g.source_mode, "origin": g.origin})
        rep.created.append(str(inc.id))


async def run_job(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Worker entry: real Profound client, LIVE mode. Commits. Never raises for 'not configured' style outcomes."""
    try:
        rep = await run_discovery_gap(session, org_id)
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    d = rep.to_dict()
    d["gaps"] = len(d["gaps"])  # job result stays small; full gaps live on the incidents
    return d
