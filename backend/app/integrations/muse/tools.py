"""The connector's tools: schemas, classification and handlers. Transport-free (no FastAPI/HTTP here), so an MCP or
other transport can wrap `TOOLS` without touching this file. Single source for the manifest and the docs.

Classification (Meta: Read / Write / Sensitive Write):
  check_change, verify_claim, check_intent, list_discovery_gaps -> READ (nothing in the domain is created or changed)
  record_feedback                                              -> WRITE (appends one audit event; idempotent)
  No Sensitive Write tool exists: approve / apply / execute / retire / delete are intentionally NOT exposed.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.changeguard import semantic
from app.changeguard import service as cg
from app.changeguard.targets import try_normalize
from app.core.audit import audit
from app.domain.enums import ActionType, IncidentCategory, IncidentState
from app.domain.errors import ChangeSetInvalid
from app.integrations.muse import errors as E
from app.integrations.muse.envelope import IntentEnvelope
from app.integrations.muse.perception import get_perception_provider
from app.models.changeguard import ChangeCheck
from app.models.core import AuditEvent, Incident

SourceMode = Literal["LIVE", "SIMULATED"]
ACTOR = "muse-connector"
READ, WRITE = "read", "write"


# ------------------------------------------------------------------------------------------------ context + base
@dataclass
class ToolContext:
    session: AsyncSession
    org_id: uuid.UUID
    source_mode: str
    request_id: str | None
    now: datetime

    def meta(self) -> dict[str, Any]:
        return {"source": "muse", "source_mode": self.source_mode, "request_id": self.request_id}


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    source: Literal["muse"] = "muse"
    source_mode: SourceMode = "LIVE"
    request_id: str | None = None


# ------------------------------------------------------------------------------------------------ shared assessment
class CanonicalBasis(BaseModel):
    key: str | None
    statement: str
    source: str | None = None
    valid_until: datetime | None = None
    relation: str | None = None


class Assessment(BaseModel):
    status: Literal["supported", "contradicted", "unknown", "degraded"]
    reasons: list[str]
    uncertain: bool = False
    basis: list[CanonicalBasis] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)


@dataclass
class _Truth:
    rows: list[dict[str, Any]]
    by_id: dict[str, Any]


async def _truth(ctx: ToolContext, scope_url: str | None = None) -> _Truth:
    norm = None
    if scope_url:
        norm = try_normalize(scope_url)
        if norm is None:
            raise E.InvalidRequest("scope_url is not a valid page URL", [{"loc": ["scope_url"], "type": "invalid_url"}])
    claims = await cg.active_canonical(ctx.session, ctx.org_id, norm, ctx.now)
    return _Truth(semantic.canonical_rows(claims), {str(c.id): c for c in claims})


def _basis(truth: _Truth, cid: str, relation: str | None) -> CanonicalBasis:
    c = truth.by_id.get(cid)
    return CanonicalBasis(key=getattr(c, "key", None), statement=getattr(c, "statement", ""),
                          source=getattr(c, "source", None), valid_until=getattr(c, "valid_until", None),
                          relation=relation)


def _run_g2(statements: list[str], canonical: list[dict[str, Any]], now: datetime) -> tuple[Any, dict[str, int]]:
    from app.changeguard.claims import extract_claims
    from app.changeguard.contradiction import evaluate_canonical

    claims, owner = [], {}
    for i, s in enumerate(statements):
        for c in extract_claims([s], id_prefix=f"s{i}"):
            claims.append(c)
            owner[c.id] = i
    res = evaluate_canonical(claims, canonical, gateway=semantic._gateway(), ranker=semantic._ranker(), now=now)
    return res, owner


async def assess(ctx: ToolContext, statements: list[str], truth: _Truth) -> list[Assessment]:
    """Compare each statement with the org's active canonical claims. Never a silent pass: failures are `degraded`."""
    if not truth.rows:
        return [Assessment(status="unknown", reasons=["no canonical truth is defined for this organization"])
                for _ in statements]
    try:
        res, owner = await asyncio.to_thread(_run_g2, statements, truth.rows, ctx.now)
    except Exception as exc:  # noqa: BLE001
        why = f"the canonical-truth comparison could not run ({type(exc).__name__}); no result is claimed"
        return [Assessment(status="degraded", reasons=[why], degraded_reasons=[type(exc).__name__])
                for _ in statements]
    out: list[Assessment] = []
    for i, _ in enumerate(statements):
        mine = {cid for cid, o in owner.items() if o == i}
        conflicts = [f for f in res.findings if f.proposed_claim_id in mine and f.type == "canonical_conflict"]
        maybe = [f for f in res.findings if f.proposed_claim_id in mine and f.type != "canonical_conflict"]
        support = [p for p in res.pair_relations if p["proposed"] in mine and p.get("relation") in ("DUPLICATE", "COMPATIBLE")
                   and not p.get("needs_semantic")]
        unresolved = [p for p in res.unresolved_pairs if p[0] in mine]
        dr = list(res.degraded_reasons) if (unresolved or res.semantic_check == "degraded") else []
        if conflicts:
            out.append(Assessment(
                status="contradicted", degraded_reasons=dr,
                reasons=[r for f in conflicts for r in (f.reasons or [])] or ["conflicts with canonical truth"],
                basis=[_basis(truth, f.canonical_claim_id, f.relation) for f in conflicts]))
        elif maybe:
            out.append(Assessment(
                status="unknown", uncertain=True, degraded_reasons=dr,
                reasons=["may contradict canonical truth; a human must decide", *[r for f in maybe for r in (f.reasons or [])]],
                basis=[_basis(truth, f.canonical_claim_id, f.relation) for f in maybe]))
        elif unresolved:
            out.append(Assessment(
                status="degraded", degraded_reasons=dr, basis=[],
                reasons=["some comparisons could not be decided (semantic layers unavailable); no pass is claimed"]))
        elif support:
            out.append(Assessment(
                status="supported", degraded_reasons=dr, reasons=["consistent with active canonical truth"],
                basis=[_basis(truth, p["canonical"], p.get("relation")) for p in support]))
        else:
            out.append(Assessment(status="unknown", degraded_reasons=dr,
                                  reasons=["active canonical truth does not address this statement"]))
    return out


# ------------------------------------------------------------------------------------------------ check_change
class CheckChangeIn(ToolInput):
    target_url: str | None = Field(default=None, max_length=2048, description="page the change would touch")
    action_type: ActionType = Field(default=ActionType.UPDATE_EXISTING_PAGE)
    proposed_claims: list[str] = Field(default_factory=list, max_length=50,
                                       description="factual claims the change would publish")
    proposed_text: str | None = Field(default=None, max_length=20000)
    reason: str = Field(default="", max_length=2000)

    @field_validator("proposed_claims")
    @classmethod
    def _claims(cls, v: list[str]) -> list[str]:
        if any(len(c) > 1000 for c in v):
            raise ValueError("each claim must be at most 1000 characters")
        return v


class CheckChangeOut(ToolOutput):
    decision: Literal["ALLOW", "MERGE", "DELAY", "REQUIRE_REVIEW", "BLOCK"]
    advisory: bool = True
    persisted: bool = False
    findings: list[dict[str, Any]]
    eligible_after: str | None = None
    merged_proposal: dict[str, Any] | None = None
    semantic_check: str
    guard_version: str
    evaluated_at: str


async def check_change(ctx: ToolContext, a: CheckChangeIn) -> CheckChangeOut:
    """Evaluate-only: build the proposal and run `cg.evaluate` (selects only). No ChangeSet/ChangeCheck row."""
    inp = cg.ChangeInput(
        org_id=ctx.org_id, agent_id="muse", agent_name="Muse connector", action_type=a.action_type.value,
        target_url=a.target_url, source_mode=ctx.source_mode, proposed_claims=a.proposed_claims,
        proposed_text=a.proposed_text, reason=a.reason, origin="muse")
    try:
        prop = cg.build_proposal(inp)
    except ChangeSetInvalid as exc:
        raise E.InvalidRequest(exc.message, exc.details) from exc
    ev = await cg.evaluate(ctx.session, prop, now=ctx.now)
    await ctx.session.rollback()  # belt and braces: a Read tool leaves nothing pending in the transaction
    return CheckChangeOut(
        **ctx.meta(), decision=ev.decision.value, findings=[f.to_json() for f in ev.findings],
        eligible_after=cg.iso(ev.eligible_after), merged_proposal=ev.merged_proposal, semantic_check=ev.semantic_check,
        guard_version=ev.guard_version, evaluated_at=cg.iso(ev.evaluated_at) or "")


# ------------------------------------------------------------------------------------------------ verify_claim
class VerifyClaimIn(ToolInput):
    claim: str = Field(min_length=3, max_length=1000, description="one factual statement to verify")
    scope_url: str | None = Field(default=None, max_length=2048, description="restrict to claims scoped to this page")


class VerifyClaimOut(ToolOutput, Assessment):
    claim: str
    canonical_claims_considered: int


async def verify_claim(ctx: ToolContext, a: VerifyClaimIn) -> VerifyClaimOut:
    truth = await _truth(ctx, a.scope_url)
    [res] = await assess(ctx, [a.claim.strip()], truth)
    return VerifyClaimOut(**ctx.meta(), **res.model_dump(), claim=a.claim.strip(),
                          canonical_claims_considered=len(truth.rows))


# ------------------------------------------------------------------------------------------------ check_intent
class IntentItem(BaseModel):
    ref: str
    statement: str
    product_truth: Literal["satisfied", "contradicted", "unknown"]
    degraded: bool = False
    uncertain: bool = False
    reasons: list[str]
    basis: list[CanonicalBasis] = Field(default_factory=list)
    ai_perception: Literal["supports", "contradicts", "silent", "unavailable"] = "unavailable"
    perception_evidence: list[str] = Field(default_factory=list)
    discovery_gap: bool = False


class CheckIntentOut(ToolOutput):
    intent: str
    expires_in_seconds: int | None = None
    ai_perception: Literal["available", "unavailable"]
    ai_perception_reason: str | None = None
    canonical_claims_considered: int
    items: list[IntentItem]
    discovery_gaps: int = 0
    unsupported_preferences_note: str = "preferences are not evaluated; they are echoed only as task context"


_TRUTH_MAP = {"supported": "satisfied", "contradicted": "contradicted", "unknown": "unknown", "degraded": "unknown"}


async def check_intent(ctx: ToolContext, env: IntentEnvelope) -> CheckIntentOut:
    pairs = env.statements()
    truth = await _truth(ctx)
    results = await assess(ctx, [s for _, s in pairs], truth)
    verdicts: dict[str, Any] = {}
    p_reason: str | None = "no perception provider is registered"
    provider = get_perception_provider()
    if provider is not None:
        try:
            verdicts = await provider.perceive(ctx.org_id, [s for _, s in pairs])
            p_reason = None
        except Exception as exc:  # noqa: BLE001
            verdicts, p_reason = {}, f"perception provider failed ({type(exc).__name__})"
    items: list[IntentItem] = []
    for (ref, stmt), r in zip(pairs, results, strict=True):
        v = verdicts.get(stmt)
        truth_state = _TRUTH_MAP[r.status]
        gap = bool(v is not None and truth_state == "satisfied" and v.state == "contradicts")
        items.append(IntentItem(
            ref=ref, statement=stmt, product_truth=truth_state, degraded=r.status == "degraded" or bool(r.degraded_reasons),
            uncertain=r.uncertain, reasons=r.reasons, basis=r.basis,
            ai_perception=(v.state if v is not None else "unavailable"),
            perception_evidence=(v.evidence[:5] if v is not None else []), discovery_gap=gap))
    ttl = env.ttl_seconds
    if env.expires_at is not None:
        exp = env.expires_at if env.expires_at.tzinfo else env.expires_at.replace(tzinfo=ctx.now.tzinfo)
        left = int((exp - ctx.now).total_seconds())
        ttl = left if ttl is None else min(ttl, left)
    return CheckIntentOut(
        **ctx.meta(), intent=env.intent, expires_in_seconds=ttl,
        ai_perception="available" if provider is not None and p_reason is None else "unavailable",
        ai_perception_reason=p_reason, canonical_claims_considered=len(truth.rows), items=items,
        discovery_gaps=sum(i.discovery_gap for i in items))


# ------------------------------------------------------------------------------------------------ list_discovery_gaps
class ListGapsIn(ToolInput):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=100000)
    include_dismissed: bool = False


class GapOut(BaseModel):
    incident_id: str
    number: int
    state: str
    severity: str
    detected_at: datetime
    canonical_key: str | None = None
    canonical_statement: str | None = None
    perceived_claim: str | None = None
    kind: str | None = None
    relation: str | None = None
    confidence: float | None = None
    engines: list[str] = Field(default_factory=list)
    occurrence: int | None = None
    evidence_grade: str | None = None
    gap_source_mode: str | None = None
    origin: str | None = None


class ListGapsOut(ToolOutput):
    items: list[GapOut]
    total: int
    limit: int
    offset: int
    note: str | None = None


async def list_discovery_gaps(ctx: ToolContext, a: ListGapsIn) -> ListGapsOut:
    q = select(Incident).where(Incident.org_id == ctx.org_id,
                               Incident.category == IncidentCategory.FACTUAL_CONFLICT.value)
    if not a.include_dismissed:
        q = q.where(Incident.state != IncidentState.DISMISSED.value)
    rows = (await ctx.session.execute(q.order_by(Incident.detected_at.desc(), Incident.id.desc()).limit(2000))
            ).scalars().all()
    gaps = [r for r in rows if isinstance((r.context or {}).get("discovery_gap"), dict)]
    page = gaps[a.offset:a.offset + a.limit]
    items = []
    for r in page:
        g = r.context["discovery_gap"]
        items.append(GapOut(
            incident_id=str(r.id), number=r.number, state=r.state, severity=r.severity, detected_at=r.detected_at,
            canonical_key=g.get("canonical_key"), canonical_statement=g.get("canonical_statement"),
            perceived_claim=g.get("perceived_claim"), kind=g.get("kind"), relation=g.get("relation"),
            confidence=g.get("confidence"), engines=list(g.get("engines") or []), occurrence=g.get("occurrence"),
            evidence_grade=g.get("evidence_grade"), gap_source_mode=g.get("source_mode"), origin=g.get("origin")))
    return ListGapsOut(**ctx.meta(), items=items, total=len(gaps), limit=a.limit, offset=a.offset,
                       note="no discovery gaps recorded for this organization" if not gaps else None)


# ------------------------------------------------------------------------------------------------ record_feedback
class RecordFeedbackIn(ToolInput):
    target_type: Literal["change_check", "discovery_gap"]
    target_id: uuid.UUID
    rating: Literal["helpful", "not_helpful", "incorrect", "other"]
    comment: str = Field(default="", max_length=1000, description="task feedback only; never personal data")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")


class RecordFeedbackOut(ToolOutput):
    feedback_id: str
    replayed: bool
    target_type: str
    target_id: str
    rating: str
    stored_as: str = "audit_event"


FEEDBACK_EVENT = "muse.feedback.recorded"


def _feedback_entity_id(org_id: uuid.UUID, key: str) -> str:
    return hashlib.sha256(f"{org_id}|{key}".encode()).hexdigest()


async def record_feedback(ctx: ToolContext, a: RecordFeedbackIn) -> RecordFeedbackOut:
    s = ctx.session
    if a.target_type == "change_check":
        ok = (await s.execute(select(func.count()).select_from(ChangeCheck).where(
            ChangeCheck.id == a.target_id, ChangeCheck.org_id == ctx.org_id))).scalar_one()
    else:
        inc = await s.get(Incident, a.target_id)
        ok = int(inc is not None and inc.org_id == ctx.org_id and inc.category == IncidentCategory.FACTUAL_CONFLICT.value
                 and "discovery_gap" in (inc.context or {}))
    if not ok:
        raise E.NotFound(f"{a.target_type} {a.target_id} not found")
    payload = {"target_type": a.target_type, "target_id": str(a.target_id), "rating": a.rating, "comment": a.comment}
    eid = _feedback_entity_id(ctx.org_id, a.idempotency_key)
    prior = (await s.execute(select(AuditEvent).where(
        AuditEvent.entity_type == "muse_feedback", AuditEvent.entity_id == eid, AuditEvent.event == FEEDBACK_EVENT)
    )).scalars().first()
    if prior is not None:
        old = prior.metadata_ or {}
        if {k: old.get(k) for k in payload} != payload:
            raise E.Conflict("idempotency_key was already used with a different payload")
        return RecordFeedbackOut(**ctx.meta(), feedback_id=str(prior.id), replayed=True, target_type=a.target_type,
                                 target_id=str(a.target_id), rating=a.rating)
    row = await audit(s, "system", ACTOR, "muse_feedback", eid, FEEDBACK_EVENT,
                      {**payload, "org_id": str(ctx.org_id), "source": "muse", "source_mode": ctx.source_mode})
    return RecordFeedbackOut(**ctx.meta(), feedback_id=str(row.id), replayed=False, target_type=a.target_type,
                             target_id=str(a.target_id), rating=a.rating)


# ------------------------------------------------------------------------------------------------ registry
@dataclass(frozen=True)
class ToolSpec:
    name: str
    slug: str
    description: str
    classification: str  # read | write
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[BaseModel]]
    scope: str
    side_effects: str
    errors: tuple[str, ...] = field(default=())
    validation_code: str = "INVALID_REQUEST"

    @property
    def path(self) -> str:
        return f"/muse/tools/{self.slug}"


_COMMON = ("MUSE_CONNECTOR_NOT_CONFIGURED", "MUSE_ORGANIZATION_NOT_CONFIGURED", "MUSE_UNAUTHORIZED", "RATE_LIMITED",
           "PAYLOAD_TOO_LARGE", "INVALID_REQUEST", "TOOL_TIMEOUT", "DEPENDENCY_UNAVAILABLE", "INTERNAL_ERROR")

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "check_change", "check-change",
        "Evaluate a proposed website/content change against the organization's active experiments, pending changes "
        "and canonical truth, and return an advisory decision (ALLOW, MERGE, DELAY, REQUIRE_REVIEW or BLOCK) with "
        "findings. Advisory only: nothing is stored, approved or applied.",
        READ, CheckChangeIn, CheckChangeOut, check_change, "change_guard:read",
        "none (evaluate-only; no change check or change set is persisted)", _COMMON),
    ToolSpec(
        "verify_claim", "verify-claim",
        "Verify one factual claim against the organization's active canonical claims. Returns supported, contradicted, "
        "unknown or degraded with reasons; never a silent pass (no canonical truth => unknown).",
        READ, VerifyClaimIn, VerifyClaimOut, verify_claim, "canonical_truth:read", "none", _COMMON),
    ToolSpec(
        "check_intent", "check-intent",
        "Given an IntentEnvelope (intent, constraints, requirements, task-relevant preferences, expiry; no identity), "
        "report for each constraint/requirement whether product truth satisfies, contradicts or does not address it, "
        "and, when AI-perception data is available, flag discovery gaps (truth says yes, AI engines say no).",
        READ, IntentEnvelope, CheckIntentOut, check_intent, "canonical_truth:read discovery_gaps:read", "none",
        (*_COMMON, "INVALID_INTENT_ENVELOPE", "IDENTITY_FIELD_REJECTED", "INTENT_EXPIRED"),
        validation_code="INVALID_INTENT_ENVELOPE"),
    ToolSpec(
        "list_discovery_gaps", "list-discovery-gaps",
        "List recorded discovery gaps (places where AI engines state something that conflicts with canonical truth) "
        "for the organization, newest first, paginated. An empty list is a valid answer.",
        READ, ListGapsIn, ListGapsOut, list_discovery_gaps, "discovery_gaps:read", "none", _COMMON),
    ToolSpec(
        "record_feedback", "record-feedback",
        "Record feedback (helpful / not_helpful / incorrect / other) on a change check or a discovery gap. Appends one "
        "audit event; idempotent on idempotency_key. Cannot change any decision, claim or incident.",
        WRITE, RecordFeedbackIn, RecordFeedbackOut, record_feedback, "feedback:write",
        "appends one audit event (no personal data); repeating the same idempotency_key returns the stored record",
        (*_COMMON, "NOT_FOUND", "IDEMPOTENCY_KEY_CONFLICT")),
)
TOOL_BY_SLUG = {t.slug: t for t in TOOLS}
