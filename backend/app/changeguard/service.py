"""Change Guard service: build a proposal, run the three checks, store the immutable ChangeSet + append-only check.

    check 1  active-experiment contamination        -> DELAY          (window service for `eligible_after`)
    check 2  duplicate / conflicting pending change -> MERGE | REQUIRE_REVIEW
    check 3  canonical-truth conflict               -> BLOCK          (G2: app/changeguard/contradiction.py)

Precedence BLOCK > DELAY > REQUIRE_REVIEW > MERGE > ALLOW; every finding is returned and stored. Callers commit.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.changeguard import digest as dg
from app.changeguard import semantic
from app.changeguard.decision import (
    ACTIVE_EXPERIMENT,
    CONFLICTING_CHANGE,
    DUPLICATE_CHANGE,
    GUARD_VERSION,
    Decision,
    Finding,
    decide,
    iso,
    latest_eligible_after,
)
from app.changeguard.overlap import (
    ClusterInfo,
    cluster_overlap,
    cluster_prompts,
    target_overlap,
)
from app.changeguard.targets import match_kind, normalize_url, target_key, try_normalize
from app.core.audit import audit
from app.core.config import get_settings
from app.domain.enums import ActionType, ExperimentStatus, IncidentState
from app.domain.errors import (
    ApprovalDigestMismatch,
    ChangeCheckKeyConflict,
    ChangeSetInvalid,
    OrganizationNotFound,
)
from app.experiments import window as vwindow
from app.models.changeguard import CanonicalClaim, ChangeCheck, ChangeSet
from app.models.core import Incident, Organization, PromptCluster
from app.models.evidence import Evidence
from app.models.interventions import Experiment, Intervention

log = structlog.get_logger()

PROTECTING = (ExperimentStatus.EXECUTING, ExperimentStatus.EXECUTED, ExperimentStatus.AWAITING_VERIFICATION)
OWN_AGENT_ID = "aeo-sre"
OWN_AGENT_NAME = "AEO SRE"
PENDING_INCIDENT_STATES = (IncidentState.INTERVENTION_PROPOSED, IncidentState.AWAITING_APPROVAL, IncidentState.APPROVED)
MAX_PROTECTED_TARGETS = 50


# ------------------------------------------------------------------------------------------------ proposal
@dataclass
class ChangeInput:
    """What the guard evaluates (API body, or an AEO SRE intervention converted by `proposal_from_intervention`)."""

    org_id: uuid.UUID
    agent_id: str
    action_type: str
    target_url: str | None = None
    agent_name: str = ""
    profound_run_id: str | None = None
    source_mode: str = "LIVE"
    proposed_claims: list[str] = field(default_factory=list)
    proposed_text: str | None = None
    proposed_diff: str | None = None
    reason: str = ""
    expected_kpi: str | None = None
    risk: str | None = None
    reversible: bool | None = None
    idempotency_key: str | None = None
    prompt_cluster_ids: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    origin: str = "external"
    intervention_id: uuid.UUID | None = None
    recheck: bool = False
    full_change_hash: str | None = None  # own interventions: hash of the WHOLE proposed_change (any edit changes it)


@dataclass
class Proposal:
    inp: ChangeInput
    target_norm: str | None
    target_key: str
    claims: list[str]  # normalized, sorted
    content_hash: str | None
    digest_dict: dict[str, Any]
    proposal_digest: str
    idempotency_key: str

    @property
    def text_blob(self) -> str:
        i = self.inp
        return "\n".join(x for x in [*i.proposed_claims, i.proposed_text or "", i.reason or ""] if x)


def build_proposal(inp: ChangeInput) -> Proposal:
    try:
        ActionType(inp.action_type)
    except ValueError as exc:
        raise ChangeSetInvalid(f"unknown action_type {inp.action_type!r}",
                               {"allowed": [a.value for a in ActionType]}) from exc
    norm = None
    if inp.target_url:
        try:
            norm = normalize_url(inp.target_url)
        except ValueError as exc:
            raise ChangeSetInvalid(f"target_url is not a valid page URL: {exc}", {"field": "target_url"}) from exc
    claims = dg.normalize_claims(inp.proposed_claims)
    chash = inp.full_change_hash or dg.text_hash(inp.proposed_text, inp.proposed_diff)
    d = dg.proposal_dict(org_id=inp.org_id, agent_id=inp.agent_id, target_key=norm or "", action_type=inp.action_type,
                         claims=claims, content_hash=chash)
    pdig = dg.proposal_digest(d)
    key = inp.idempotency_key or f"{inp.agent_id}:{inp.profound_run_id or '-'}:{pdig}"
    return Proposal(inp, norm, target_key(norm), claims, chash, d, pdig, key[:255])


# ------------------------------------------------------------------------------------------------ evaluation
@dataclass
class Evaluation:
    decision: Decision
    findings: list[Finding]
    eligible_after: datetime | None
    merged_proposal: dict[str, Any] | None
    semantic_check: str
    action_digest: str
    experiment_context: list[dict[str, Any]]
    experiment_refs: list[str]
    guard_version: str = GUARD_VERSION
    evaluated_at: datetime = field(default_factory=vwindow.now)


@dataclass
class _Protecting:
    exp: Experiment
    inc: Incident
    targets: list[str]
    cluster: ClusterInfo | None


async def experiment_targets(session: AsyncSession, exp: Experiment, inc: Incident) -> list[str]:
    """Pages an experiment protects: its explicit target, else the owned pages its incident's evidence names."""
    out: list[str] = []
    key = exp.target_key or ""
    if key.startswith("target:"):
        n = try_normalize(key[len("target:"):])
        if n:
            out.append(n)
    change = exp.proposed_change if isinstance(exp.proposed_change, dict) else {}
    explicit = change.get("target_url") or (change.get("manual_task") or {}).get("target_url")
    n = try_normalize(explicit)
    if n:
        out.append(n)
    if not out:
        rows = (await session.execute(
            select(Evidence.url).where(Evidence.incident_id == inc.id, Evidence.type == "owned",
                                       Evidence.url.is_not(None)).limit(MAX_PROTECTED_TARGETS * 2))).scalars().all()
        out = [n for n in (try_normalize(u) for u in rows) if n]
    return list(dict.fromkeys(out))[:MAX_PROTECTED_TARGETS]


async def _cluster(session: AsyncSession, inc: Incident) -> ClusterInfo | None:
    if inc.prompt_cluster_id is None:
        return None
    pc = await session.get(PromptCluster, inc.prompt_cluster_id)
    if pc is None:
        return None
    return ClusterInfo(str(pc.id), pc.topic, cluster_prompts(pc.prompts))


async def protecting_experiments(session: AsyncSession, org_id: uuid.UUID, *,
                                 exclude_intervention_id: uuid.UUID | None = None) -> list[_Protecting]:
    stmt = (select(Experiment, Incident).join(Incident, Incident.id == Experiment.incident_id)
            .where(Incident.org_id == org_id, Experiment.status.in_(PROTECTING), Experiment.dry_run.is_(False))
            .order_by(Experiment.number))
    if exclude_intervention_id is not None:
        stmt = stmt.where(Experiment.intervention_id != exclude_intervention_id)
    out = []
    for exp, inc in (await session.execute(stmt)).all():
        out.append(_Protecting(exp, inc, await experiment_targets(session, exp, inc), await _cluster(session, inc)))
    return out


def eligible_for(exp: Experiment, now: datetime) -> tuple[datetime | None, str]:
    """When may this experiment's scope be changed again? From the ONE window service; no time is invented."""
    start, end = exp.verification_window_start, exp.verification_window_end
    if start is not None:
        if not vwindow.attempt_allowed(start, now, dry_run=False).ok:
            return vwindow.aware(start), "window_start"  # measurement cannot start before this
        if end is not None and vwindow.aware(end) > now:
            return vwindow.aware(end), "window_end"  # window open, outcome not measured yet
        return None, "verification_overdue"
    return vwindow.window_from_settings().bounds(now)[0], "earliest_possible_window_start"


def _check_experiments(prop: Proposal, protecting: list[_Protecting], now: datetime, *, observe_exempt: bool
                       ) -> tuple[list[Finding], list[dict[str, Any]]]:
    findings: list[Finding] = []
    context: list[dict[str, Any]] = []
    for p in protecting:
        t = target_overlap(prop.target_norm, p.targets)
        c = cluster_overlap(p.cluster, change_cluster_ids=prop.inp.prompt_cluster_ids,
                            change_prompts=prop.inp.prompts, text_blob=prop.text_blob)
        if t.pct <= 0 and c.pct <= 0:
            continue
        exp = p.exp
        status = ExperimentStatus(exp.status).value
        action = ActionType(exp.selected_action).value
        until, basis = eligible_for(exp, now)
        context.append({"experiment_id": str(exp.id), "status": status})
        observe = action == ActionType.OBSERVE.value
        what = "an OBSERVE baseline" if observe else f"a {action} experiment"
        reason = (f"{exp.code} is {what} ({status}) whose scope this change touches "
                  f"(target overlap {t.pct:g}%, prompt-cluster overlap {c.pct:g}%): changing it before the "
                  f"measurement completes would contaminate the result")
        if until is not None:
            reason += f"; eligible after {iso(until)}"
        else:
            reason += "; verification is overdue, no completion time can be given"
        overdue = until is None
        if overdue:
            reason = reason.replace("verification is overdue, no completion time can be given",
                                    "its verification is overdue, so no completion time can be given; a human must "
                                    "decide (never a DELAY without a time)")
        findings.append(Finding(
            ACTIVE_EXPERIMENT,
            Decision.ALLOW if observe_exempt else (Decision.REQUIRE_REVIEW if overdue else Decision.DELAY),
            reason if not observe_exempt else reason + " (informational: observe changes nothing)",
            references={"experiment_id": str(exp.id), "experiment_code": exp.code,
                        "incident_id": str(exp.incident_id)},
            eligible_after=None if observe_exempt else until,
            details={"experiment_status": status, "experiment_action": action, "observe_baseline": observe,
                     "target_overlap_pct": t.pct, "prompt_cluster_overlap_pct": c.pct,
                     "cluster_overlap_basis": c.basis, "matched_targets": t.matches[:10],
                     "protected_targets_total": t.protected_total, "eligible_after_basis": basis,
                     "window_start": iso(exp.verification_window_start), "window_end": iso(exp.verification_window_end)}))
    return findings, context


# ------------------------------------------------------------------------------------------------ check 2
@dataclass
class _Pending:
    kind: str  # change_set | intervention
    ref_id: uuid.UUID
    agent_id: str
    agent_name: str
    target_norm: str
    action: str
    claims: list[str]
    text_hash: str | None
    text: str | None
    source_mode: str


async def _pending_changes(session: AsyncSession, prop: Proposal, now: datetime,
                           exclude_change_set_id: uuid.UUID | None) -> list[_Pending]:
    out: list[_Pending] = []
    inp = prop.inp
    ttl = timedelta(hours=float(get_settings().change_guard_pending_ttl_hours))
    sets = (await session.execute(
        select(ChangeSet).where(ChangeSet.org_id == inp.org_id, ChangeSet.origin == "external",
                                ChangeSet.created_at >= now - ttl, ChangeSet.target_key != ""))).scalars().all()
    for cs in sets:
        if cs.id == exclude_change_set_id or cs.proposal_digest == prop.proposal_digest:
            continue
        latest = (await session.execute(select(ChangeCheck.decision).where(ChangeCheck.change_set_id == cs.id)
                                        .order_by(ChangeCheck.created_at.desc(), ChangeCheck.id.desc()).limit(1))
                  ).scalar()
        if latest not in (Decision.ALLOW.value, Decision.REQUIRE_REVIEW.value):
            continue  # BLOCK/DELAY were told to stop; MERGE was told to fold into another change
        n = try_normalize(cs.target_url)
        if n:
            out.append(_Pending("change_set", cs.id, cs.agent_id, cs.agent_name, n, cs.action_type,
                                list(cs.proposed_claims or []), cs.text_hash, cs.proposed_text, cs.source_mode))
    ivs = (await session.execute(
        select(Intervention, Incident).join(Incident, Incident.id == Intervention.incident_id)
        .where(Incident.org_id == inp.org_id, Intervention.selected.is_(True),
               Intervention.action != ActionType.OBSERVE,
               Incident.state.in_([s.value for s in PENDING_INCIDENT_STATES])))).all()
    for iv, _inc in ivs:
        if inp.intervention_id is not None and iv.id == inp.intervention_id:
            continue
        ci = proposal_input_from_change(iv.proposed_change or {}, org_id=inp.org_id, action=ActionType(iv.action).value)
        n = try_normalize(ci.target_url)
        if n:
            claims = dg.normalize_claims(ci.proposed_claims)
            out.append(_Pending("intervention", iv.id, OWN_AGENT_ID, OWN_AGENT_NAME, n, ActionType(iv.action).value,
                                claims, dg.text_hash(ci.proposed_text, ci.proposed_diff), ci.proposed_text, "LIVE"))
    return out


def _claims_relation(mine: list[str], theirs: list[str]) -> str:
    """identical | subset | compatible | conflicting | unknown. Pairwise relations from G2's deterministic rules;
    anything the rules cannot place is `unknown` (-> human review), never a silent merge."""
    if mine == theirs:
        return "identical" if mine else "no_claims"
    if set(mine) <= set(theirs) or set(theirs) <= set(mine):
        return "subset"
    try:
        from app.changeguard.claims import extract_claims
        from app.changeguard.contradiction import compare_claims

        a, b = extract_claims(mine), extract_claims(theirs)
        rels = [compare_claims(x, y) for x in a for y in b]
    except Exception:  # noqa: BLE001
        return "unknown"
    if any(r.relation == "CONFLICTING" for r in rels):
        return "conflicting"
    if rels and all(r.relation in ("DUPLICATE", "COMPATIBLE", "DEPENDENT", "UNRELATED") and not r.needs_semantic
                    for r in rels):
        return "compatible"
    return "unknown"


def _check_pending(prop: Proposal, pending: list[_Pending]) -> tuple[list[Finding], dict[str, Any] | None]:
    findings: list[Finding] = []
    merge_from: list[_Pending] = []
    if not prop.target_norm:
        return findings, None
    for p in pending:
        kind = match_kind(prop.target_norm, p.target_norm)
        if kind is None:
            continue
        same_action = p.action == prop.inp.action_type
        rel = _claims_relation(prop.claims, p.claims)
        same_text = bool(prop.content_hash) and prop.content_hash == p.text_hash
        mergeable = (kind == "exact" and same_action and (
            rel in ("identical", "subset", "compatible") or (rel == "no_claims" and same_text)))
        refs = {("change_set_id" if p.kind == "change_set" else "intervention_id"): str(p.ref_id),
                "agent_id": p.agent_id}
        det = {"target_match": kind, "other_action": p.action, "claims_relation": rel,
               "other_kind": p.kind, "other_source_mode": p.source_mode, "other_agent_name": p.agent_name}
        who = f"{p.agent_name or p.agent_id}'s pending {p.action} ({p.kind.replace('_', ' ')})"
        if mergeable:
            merge_from.append(p)
            findings.append(Finding(DUPLICATE_CHANGE, Decision.MERGE,
                                    f"{who} targets the same page with compatible claims ({rel}): merge them into "
                                    f"one change", references=refs, details=det))
        else:
            why = ("it targets a different part of the same section" if kind == "prefix" else
                   "the action types differ (different intents)" if not same_action else
                   "its claims conflict with this change" if rel == "conflicting" else
                   "compatibility of the claims could not be established")
            findings.append(Finding(CONFLICTING_CHANGE, Decision.REQUIRE_REVIEW,
                                    f"{who} overlaps this change's target and {why}: a human must reconcile them",
                                    references=refs, details=det))
    merged = None
    if merge_from and all(f.decision == Decision.MERGE for f in findings):
        claims = sorted({*prop.claims, *(c for p in merge_from for c in p.claims)})
        texts = {t for t in [prop.inp.proposed_text, *(p.text for p in merge_from)] if t}
        merged = {
            "target_url": prop.target_norm, "action_type": prop.inp.action_type, "proposed_claims": claims,
            "proposed_text": next(iter(texts)) if len(texts) == 1 else None,
            "needs_manual_text_merge": len(texts) > 1,
            "merged_from": [{"kind": "this_change", "agent_id": prop.inp.agent_id}] + [
                {"kind": p.kind, "id": str(p.ref_id), "agent_id": p.agent_id} for p in merge_from],
        }
    return findings, merged


# ------------------------------------------------------------------------------------------------ check 3
async def active_canonical(session: AsyncSession, org_id: uuid.UUID, target_norm: str | None,
                           now: datetime) -> list[CanonicalClaim]:
    rows = (await session.execute(select(CanonicalClaim).where(
        CanonicalClaim.org_id == org_id, CanonicalClaim.status == "active").order_by(CanonicalClaim.key))
    ).scalars().all()
    out = []
    for r in rows:
        if r.valid_from is not None and vwindow.aware(r.valid_from) > now:
            continue
        if r.valid_until is not None and vwindow.aware(r.valid_until) < now:
            continue  # expired
        scope = (r.scope or "").strip()
        sn = try_normalize(scope) if scope and scope != "*" else None
        if sn and target_norm and match_kind(sn, target_norm) is None:
            continue  # a URL-scoped claim only applies to changes on that section
        out.append(r)
    return out


def _evaluation_claims(prop: Proposal) -> list[str]:
    """Claims the canonical check reads: the agent's list as written + sentences of the proposed text."""
    texts = [c for c in prop.inp.proposed_claims if c and c.strip()]
    if prop.inp.proposed_text:
        try:
            from app.changeguard.claims import split_sentences

            texts += [s for s, _ in split_sentences(prop.inp.proposed_text)][:100]
        except Exception:  # noqa: BLE001
            pass
    return list(dict.fromkeys(texts))


# ------------------------------------------------------------------------------------------------ orchestration
async def lock_target(session: AsyncSession, prop: Proposal) -> None:
    """Serialize evaluations per (org, target) so a concurrent twin sees this change as pending (Postgres advisory
    transaction lock, released at commit/rollback)."""
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    key = f"{prop.inp.org_id}|{prop.target_norm or ''}"
    await session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})


async def evaluate(session: AsyncSession, prop: Proposal, *, exclude_change_set_id: uuid.UUID | None = None,
                   now: datetime | None = None) -> Evaluation:
    now = now or vwindow.now()
    inp = prop.inp
    observe = inp.action_type == ActionType.OBSERVE.value
    protecting = await protecting_experiments(session, inp.org_id, exclude_intervention_id=inp.intervention_id)
    f1, context = _check_experiments(prop, protecting, now, observe_exempt=observe)
    f2: list[Finding] = []
    merged = None
    if not observe:
        pend = await _pending_changes(session, prop, now, exclude_change_set_id)
        f2, merged = _check_pending(prop, pend)
    canon = await active_canonical(session, inp.org_id, prop.target_norm, now)
    sem = await semantic.evaluate(_evaluation_claims(prop) if not observe else [],
                                  semantic.canonical_rows(canon), now)
    findings = [*f1, *f2, *sem.findings]
    decision = decide(findings)
    refs = sorted({v for f in findings for k, v in f.references.items()
                   if k in ("experiment_id", "experiment_code") and v})
    return Evaluation(
        decision=decision, findings=findings, eligible_after=latest_eligible_after(findings),
        merged_proposal=merged if decision == Decision.MERGE else None, semantic_check=sem.state,
        action_digest=dg.action_digest(prop.digest_dict, context), experiment_context=dg.normalize_context(context),
        experiment_refs=refs, evaluated_at=now)


@dataclass
class Submitted:
    change_set: ChangeSet
    check: ChangeCheck
    replayed: bool


def _new_change_set(prop: Proposal, now: datetime) -> ChangeSet:
    i = prop.inp
    return ChangeSet(
        id=uuid.uuid4(), org_id=i.org_id, origin=i.origin, intervention_id=i.intervention_id, agent_id=i.agent_id,
        agent_name=i.agent_name, profound_run_id=i.profound_run_id, source_mode=i.source_mode, target_url=i.target_url,
        target_key=prop.target_key, action_type=i.action_type, proposed_claims=prop.claims,
        claims_raw=list(i.proposed_claims), prompt_cluster_ids=[str(c) for c in i.prompt_cluster_ids],
        prompts=list(i.prompts), proposed_text=i.proposed_text, proposed_diff=i.proposed_diff,
        text_hash=prop.content_hash, reason=i.reason, expected_kpi=i.expected_kpi, risk=i.risk,
        reversible=i.reversible, idempotency_key=prop.idempotency_key, proposal_digest=prop.proposal_digest,
        created_at=now)


def _new_check(cs: ChangeSet, ev: Evaluation) -> ChangeCheck:
    return ChangeCheck(
        id=uuid.uuid4(), change_set_id=cs.id, org_id=cs.org_id, decision=ev.decision.value,
        findings=[f.to_json() for f in ev.findings], eligible_after=ev.eligible_after,
        merged_proposal=ev.merged_proposal, semantic_check=ev.semantic_check, guard_version=ev.guard_version,
        action_digest=ev.action_digest, experiment_context=ev.experiment_context, experiment_refs=ev.experiment_refs,
        evaluated_at=ev.evaluated_at, created_at=ev.evaluated_at)


async def latest_check(session: AsyncSession, change_set_id: uuid.UUID) -> ChangeCheck | None:
    return (await session.execute(select(ChangeCheck).where(ChangeCheck.change_set_id == change_set_id)
                                  .order_by(ChangeCheck.created_at.desc(), ChangeCheck.id.desc()).limit(1))
            ).scalars().first()


async def _by_key(session: AsyncSession, org_id: uuid.UUID, key: str) -> ChangeSet | None:
    return (await session.execute(select(ChangeSet).where(ChangeSet.org_id == org_id,
                                                          ChangeSet.idempotency_key == key))).scalars().first()


def _audit_actor(prop: Proposal) -> tuple[str, str]:
    return ("system", f"agent:{prop.inp.agent_id}") if prop.inp.origin == "external" else ("system", OWN_AGENT_ID)


async def _record(session: AsyncSession, prop: Proposal, cs: ChangeSet, check: ChangeCheck) -> None:
    actor_type, actor = _audit_actor(prop)
    base = {"org_id": str(cs.org_id), "change_set_id": str(cs.id), "source_mode": cs.source_mode,
            "agent_id": cs.agent_id, "origin": cs.origin}
    await audit(session, actor_type, actor, "change_check", check.id, "change_check.created",
                {**base, "target": cs.target_key, "action_type": cs.action_type})
    await audit(session, actor_type, actor, "change_check", check.id, "change_check.decided",
                {**base, "decision": check.decision, "finding_types": sorted({f["type"] for f in check.findings}),
                 "eligible_after": iso(check.eligible_after), "guard_version": check.guard_version,
                 "semantic_check": check.semantic_check})


async def submit(session: AsyncSession, inp: ChangeInput) -> Submitted:
    """Create + evaluate a ChangeSet. Idempotent on (org, idempotency_key): same proposal digest -> the stored
    decision (`replayed`); a different digest under the same key -> ChangeCheckKeyConflict. `recheck=True` appends a
    FRESH evaluation to the stored ChangeSet instead of replaying (e.g. after a DELAY elapsed)."""
    prop = build_proposal(inp)
    await lock_target(session, prop)
    existing = await _by_key(session, inp.org_id, prop.idempotency_key)
    if existing is not None:
        return await _replay_or_recheck(session, prop, existing)
    ev = await evaluate(session, prop)
    cs = _new_change_set(prop, ev.evaluated_at)
    try:
        async with session.begin_nested():  # unique (org_id, idempotency_key): a concurrent twin may win the insert
            session.add(cs)
            await session.flush()
    except IntegrityError:
        winner = await _by_key(session, inp.org_id, prop.idempotency_key)
        if winner is None:
            raise
        return await _replay_or_recheck(session, prop, winner)
    check = _new_check(cs, ev)
    session.add(check)
    await session.flush()
    await _record(session, prop, cs, check)
    from app.control_policy.shadow import record_after_check

    await record_after_check(session, cs, check)
    return Submitted(cs, check, False)


async def _replay_or_recheck(session: AsyncSession, prop: Proposal, existing: ChangeSet) -> Submitted:
    if existing.proposal_digest != prop.proposal_digest:
        raise ChangeCheckKeyConflict(
            "this idempotency_key was already used for a different change", {
                "idempotency_key": prop.idempotency_key, "stored_proposal_digest": existing.proposal_digest,
                "request_proposal_digest": prop.proposal_digest, "change_set_id": str(existing.id)})
    if prop.inp.recheck:
        ev = await evaluate(session, prop, exclude_change_set_id=existing.id)
        check = _new_check(existing, ev)
        session.add(check)
        await session.flush()
        await _record(session, prop, existing, check)
        from app.control_policy.shadow import record_after_check

        await record_after_check(session, existing, check)
        return Submitted(existing, check, False)
    check = await latest_check(session, existing.id)
    if check is None:  # cannot happen (set + check are one transaction); fail loudly rather than invent a decision
        raise ChangeSetInvalid("stored change set has no check", {"change_set_id": str(existing.id)})
    return Submitted(existing, check, True)


async def resolve_org(session: AsyncSession, org_id: uuid.UUID | None, org_domain: str | None) -> Organization:
    if org_id is None and not org_domain:
        raise ChangeSetInvalid("org_id or org_domain is required", {"field": "org_id"})
    if org_id is not None:
        org = await session.get(Organization, org_id)
    else:
        domain = (org_domain or "").strip().lower().removeprefix("www.")
        org = (await session.execute(select(Organization).where(Organization.domain == domain))).scalars().first()
    if org is None:
        raise OrganizationNotFound("organization not found", {"org_id": str(org_id) if org_id else None,
                                                              "org_domain": org_domain})
    return org


# ------------------------------------------------------------------------------------------------ own interventions
def _change_text(change: dict[str, Any]) -> str:
    parts: list[str] = []
    for f in (change.get("files") or [])[:5]:
        if isinstance(f, dict) and f.get("new_content"):
            parts.append(str(f["new_content"]))
    task = change.get("manual_task") or {}
    if isinstance(task, dict) and task.get("body"):
        parts.append(str(task["body"]))
    return "\n".join(parts)[:20_000]


def proposal_input_from_change(change: dict[str, Any], *, org_id: uuid.UUID, action: str,
                               intervention_id: uuid.UUID | None = None, title: str = "", risk: str | None = None
                               ) -> ChangeInput:
    """AEO SRE's own proposed_change as a ChangeInput. Claims = sentences of the proposed content (G2 extraction)."""
    change = change if isinstance(change, dict) else {}
    target = change.get("target_url") or (change.get("manual_task") or {}).get("target_url")
    text = _change_text(change)
    claims: list[str] = []
    if text:
        try:
            from app.changeguard.claims import split_sentences

            claims = [s for s, _ in split_sentences(text)][:60]
        except Exception:  # noqa: BLE001
            claims = []
    diff = change.get("diff")
    return ChangeInput(
        org_id=org_id, agent_id=OWN_AGENT_ID, agent_name=OWN_AGENT_NAME, action_type=action,
        target_url=target if try_normalize(target) else None, source_mode="LIVE", proposed_claims=claims,
        proposed_text=text or None, proposed_diff=diff if isinstance(diff, str) and diff else None,
        reason=title or str(change.get("summary") or "")[:500], risk=risk, origin="intervention",
        intervention_id=intervention_id, full_change_hash=dg.sha256_hex(dg.canonical_json(change)))


async def intervention_proposal(session: AsyncSession, iv: Intervention, change: dict[str, Any] | None = None
                                ) -> Proposal:
    inc = await session.get(Incident, iv.incident_id)
    inp = proposal_input_from_change(
        change if change is not None else (iv.proposed_change or {}), org_id=inc.org_id,
        action=ActionType(iv.action).value, intervention_id=iv.id, title=iv.title or "",
        risk=getattr(iv.risk, "value", iv.risk))
    if inc.prompt_cluster_id is not None:  # B4's cluster scope: same (org, prompt cluster) as another active experiment
        inp.prompt_cluster_ids = [str(inc.prompt_cluster_id)]
    prop = build_proposal(inp)
    prop.idempotency_key = f"intervention:{iv.id}:{prop.proposal_digest}"[:255]
    return prop


async def evaluate_intervention(session: AsyncSession, iv: Intervention, *, change: dict[str, Any] | None = None
                                ) -> Submitted:
    """Run the guard on AEO SRE's own proposal. One ChangeSet per distinct proposal; every call appends a fresh check
    (the verdict at approval time must reflect the experiments active NOW)."""
    prop = await intervention_proposal(session, iv, change)
    await lock_target(session, prop)
    cs = await _by_key(session, prop.inp.org_id, prop.idempotency_key)
    ev = await evaluate(session, prop, exclude_change_set_id=cs.id if cs else None)
    if cs is None:
        cs = _new_change_set(prop, ev.evaluated_at)
        try:
            async with session.begin_nested():
                session.add(cs)
                await session.flush()
        except IntegrityError:
            cs = await _by_key(session, prop.inp.org_id, prop.idempotency_key)
            if cs is None:
                raise
    check = _new_check(cs, ev)
    session.add(check)
    await session.flush()
    await _record(session, prop, cs, check)
    return Submitted(cs, check, False)


async def latest_intervention_check(session: AsyncSession, intervention_id: uuid.UUID
                                    ) -> tuple[ChangeSet, ChangeCheck] | None:
    row = (await session.execute(
        select(ChangeSet, ChangeCheck).join(ChangeCheck, ChangeCheck.change_set_id == ChangeSet.id)
        .where(ChangeSet.intervention_id == intervention_id)
        .order_by(ChangeCheck.created_at.desc(), ChangeCheck.id.desc()).limit(1))).first()
    return (row[0], row[1]) if row else None


def blocks_approval(decision: str) -> bool:
    return decision in (Decision.BLOCK.value, Decision.DELAY.value)


# ------------------------------------------------------------------------------------------------ approval binding
async def verify_binding(session: AsyncSession, iv: Intervention, approval: Any | None) -> None:
    """The change about to be activated/executed must be the change that was approved (or, for `observe`, the change
    the guard saw when it was proposed). Mismatch -> ApprovalDigestMismatch (409). Legacy approvals without a digest
    cannot be verified and are allowed with a warning (they pre-date Change Guard)."""
    if approval is not None:
        bound_digest = getattr(approval, "action_digest", None)
        if not bound_digest:
            log.warning("changeguard.approval_without_digest", approval_id=str(approval.id))
            return
        change = (approval.modified_change if approval.status == "modified" and approval.modified_change
                  else iv.proposed_change) or {}
        prop = await intervention_proposal(session, iv, change)
        bound = (await session.execute(select(ChangeCheck).where(ChangeCheck.action_digest == bound_digest)
                                       .limit(1))).scalars().first()
        current = dg.action_digest(prop.digest_dict, bound.experiment_context) if bound is not None else None
        if bound is None or current != bound_digest:
            raise ApprovalDigestMismatch(
                "the change differs from what was approved; the approval no longer applies - approve the current "
                "proposal again", {"intervention_id": str(iv.id), "approval_id": str(approval.id),
                                   "approved_digest": bound_digest, "current_digest": current})
        return
    latest = await latest_intervention_check(session, iv.id)
    if latest is None:
        return
    cs, _check = latest
    prop = await intervention_proposal(session, iv)
    if prop.proposal_digest != cs.proposal_digest:
        # observe has no approval: compare with the most recent guard record for this intervention
        any_match = (await session.execute(select(ChangeSet.id).where(
            ChangeSet.intervention_id == iv.id, ChangeSet.proposal_digest == prop.proposal_digest).limit(1))
        ).first()
        if any_match is None:
            raise ApprovalDigestMismatch(
                "the change was modified after it was checked; run the check again",
                {"intervention_id": str(iv.id), "checked_digest": cs.proposal_digest,
                 "current_digest": prop.proposal_digest})


# ------------------------------------------------------------------------------------------------ read models
def check_summary(cs: ChangeSet, check: ChangeCheck, *, experiment_ref: str | None = None) -> dict[str, Any]:
    reasons = [f["reason"] for f in (check.findings or [])
               if experiment_ref is None or experiment_ref in (f.get("references") or {}).values()]
    return {
        "id": str(check.id), "change_set_id": str(cs.id), "decision": check.decision, "agent_id": cs.agent_id,
        "agent_name": cs.agent_name, "source_mode": cs.source_mode, "origin": cs.origin, "target": cs.target_url,
        "action_type": cs.action_type, "reasons": reasons[:5], "eligible_after": iso(check.eligible_after),
        "created_at": iso(check.created_at)}


async def protection_for(session: AsyncSession, exp: Experiment) -> dict[str, Any]:
    """`protection` block of the experiment detail: is this experiment protected from new changes, until when, which
    pages, how many checks it held back and the most recent checks that touched it."""
    inc = await session.get(Incident, exp.incident_id)
    now = vwindow.now()
    targets = await experiment_targets(session, exp, inc)
    protected = ExperimentStatus(exp.status) in PROTECTING and not exp.dry_run
    until, basis = eligible_for(exp, now) if protected else (None, None)
    from app.api.mappers import exp_code

    code = exp_code(exp.number, exp.id)
    col = cast(ChangeCheck.experiment_refs, String)
    ref_match = or_(col.contains(f'"{exp.id}"'), col.contains(f'"{code}"'))
    held = (await session.execute(select(func.count()).select_from(ChangeCheck).where(
        ref_match, ChangeCheck.decision.in_([Decision.DELAY.value, Decision.BLOCK.value])))).scalar_one()
    rows = (await session.execute(
        select(ChangeSet, ChangeCheck).join(ChangeCheck, ChangeCheck.change_set_id == ChangeSet.id)
        .where(ref_match).order_by(ChangeCheck.created_at.desc(), ChangeCheck.id.desc()).limit(10))).all()
    return {"protected": protected, "until": iso(until), "until_basis": basis, "targets": targets,
            "checks_blocked_count": int(held),
            "recent_checks": [check_summary(cs, ck, experiment_ref=str(exp.id)) for cs, ck in rows]}


async def verdict_for_intervention(session: AsyncSession, iv: Intervention) -> dict[str, Any] | None:
    """Stored guard verdict for AEO SRE's own proposal (read-only; None = never checked => 'unavailable')."""
    latest = await latest_intervention_check(session, iv.id)
    if latest is None:
        return None
    cs, check = latest
    prop = await intervention_proposal(session, iv)
    return {
        "check_id": str(check.id), "decision": check.decision, "findings": check.findings or [],
        "eligible_after": iso(check.eligible_after), "merged_proposal": check.merged_proposal,
        "semantic_check": check.semantic_check, "guard_version": check.guard_version,
        "digest": check.action_digest, "evaluated_at": iso(check.evaluated_at),
        "stale": prop.proposal_digest != cs.proposal_digest,
        "blocks_approval": blocks_approval(check.decision) and ActionType(iv.action) != ActionType.OBSERVE,
        "requires_review_reason": check.decision == Decision.REQUIRE_REVIEW.value,
    }
