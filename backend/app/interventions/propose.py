"""Build candidate Intervention rows for every ActionType from a policy decision + incident evidence."""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionType, EvidenceStatus, EvidenceType, HypothesisStatus, Risk, SelectionBasis
from app.interventions.changes import ProposedChange, validate_change
from app.interventions.facts import EvidenceItem, build_fact_base
from app.interventions.llm import LLMClient, default_llm_client
from app.interventions.patching import generate_patch
from app.interventions.templates import PlanContext, build_template, slugify
from app.models.interventions import Approval, Intervention

log = structlog.get_logger()
OldContentLoader = Callable[[str], Awaitable[str | None]]

_BASE_RISK: dict[ActionType, Risk] = {
    ActionType.OBSERVE: Risk.LOW,
    ActionType.UPDATE_EXISTING_PAGE: Risk.LOW,
    ActionType.CREATE_FAQ: Risk.LOW,
    ActionType.STRUCTURED_DATA: Risk.LOW,
    ActionType.CREATE_CANONICAL_PAGE: Risk.MEDIUM,
    ActionType.CREATE_COMPARISON_CONTENT: Risk.MEDIUM,
    ActionType.PUBLISHER_OUTREACH: Risk.MEDIUM,
}
_LABEL: dict[ActionType, str] = {
    ActionType.OBSERVE: "Observe (no change)",
    ActionType.UPDATE_EXISTING_PAGE: "Update existing page",
    ActionType.CREATE_FAQ: "Add FAQ content",
    ActionType.CREATE_CANONICAL_PAGE: "Create canonical page",
    ActionType.CREATE_COMPARISON_CONTENT: "Create comparison content",
    ActionType.PUBLISHER_OUTREACH: "Publisher outreach",
    ActionType.STRUCTURED_DATA: "Structured data update",
}
_WHY: dict[ActionType, str] = {
    ActionType.OBSERVE: "Evidence or impact does not justify a change yet; keep measuring.",
    ActionType.UPDATE_EXISTING_PAGE: "Strengthen an existing owned page with explicit, evidence-backed capability content.",
    ActionType.CREATE_FAQ: "Publish question-and-answer content that states the verified facts directly.",
    ActionType.CREATE_CANONICAL_PAGE: "Create an authoritative owned page where none carries the verified facts.",
    ActionType.CREATE_COMPARISON_CONTENT: "Publish a factual comparison citing owned facts and quoted competitor statements.",
    ActionType.PUBLISHER_OUTREACH: "Ask a third-party publisher to correct or update a cited statement.",
    ActionType.STRUCTURED_DATA: "Expose verified facts as machine-readable structured data.",
}
_RISK_ORDER = [Risk.LOW, Risk.MEDIUM, Risk.HIGH]


@dataclass
class NormDecision:
    selected: ActionType
    scores: dict[ActionType, float]
    selection_basis: SelectionBasis | None = None
    probability: float | None = None
    policy_version: str | None = None
    policy_version_id: uuid.UUID | None = None
    cold_start: bool | None = None
    detail: dict[ActionType, dict[str, float]] = field(default_factory=dict)


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v is not None:
            return v
    return default


def normalize_decision(decision: Any) -> NormDecision:
    """Accept `app.policy.Decision`, a `PolicyDecision` row, or a plain dict."""
    selected = ActionType(_get(decision, "action", "selected_action"))
    scores: dict[ActionType, float] = {}
    detail: dict[ActionType, dict[str, float]] = {}
    raw = _get(decision, "scores", default=[])
    items = [(k, v) for k, v in raw.items()] if isinstance(raw, dict) else [(None, s) for s in raw]
    for key, s in items:
        act = ActionType(key if key is not None else _get(s, "action"))
        if isinstance(s, (int, float)):
            scores[act] = float(s)
            continue
        prob, mean, ucb = (_get(s, n) for n in ("probability", "mean", "ucb"))
        scores[act] = float(prob if prob is not None else mean if mean is not None else _get(s, "score", default=0.0))
        detail[act] = {k: float(v) for k, v in (("probability", prob), ("mean", mean), ("ucb", ucb)) if v is not None}
    basis = _get(decision, "selection_basis")
    vid = _get(decision, "policy_version_id")
    return NormDecision(
        selected=selected, scores=scores, selection_basis=SelectionBasis(basis) if basis else None,
        probability=_get(decision, "probability"),
        policy_version=_get(decision, "policy_version") if isinstance(_get(decision, "policy_version"), str) else None,
        policy_version_id=vid if isinstance(vid, uuid.UUID) else None, cold_start=_get(decision, "cold_start"),
        detail=detail,
    )


def _bump(risk: Risk) -> Risk:
    return _RISK_ORDER[min(_RISK_ORDER.index(risk) + 1, 2)]


def assess_risk(action: ActionType, *, root_cause_confirmed: bool, has_facts: bool, generated_by_llm: bool) -> Risk:
    risk = _BASE_RISK[action]
    if action is ActionType.OBSERVE:
        return risk
    if not root_cause_confirmed:
        risk = _bump(risk)
    if not has_facts:
        risk = _bump(risk)
    return risk


@dataclass
class Candidate:
    action: ActionType
    title: str
    rationale: str
    risk: Risk
    score: float
    selected: bool
    change: ProposedChange | None
    unavailable_reason: str | None = None
    used_llm: bool = False


def _derive_capability(incident: Any, cluster_topic: str | None, hyp_title: str | None) -> str:
    ctx = _get(incident, "context", default={}) or {}
    return str(ctx.get("capability") or cluster_topic or hyp_title or _get(incident, "title", default="the topic"))


def _rationale(action: ActionType, *, hyp: tuple[str, str] | None, nd: NormDecision, selected: bool,
               ev: list[EvidenceItem], reason: str | None, score: float) -> str:
    parts = [_WHY[action]]
    if hyp:
        title, status = hyp
        parts.append(f"Root cause ({status}"
                     f"{'' if status == HypothesisStatus.CONFIRMED.value else ': not yet confirmed by the evidence gate'}): {title}.")
    else:
        parts.append("No root-cause hypothesis is attached; treat this as unconfirmed.")
    if ev:
        parts.append(f"Based on {len(ev)} evidence item(s): " + "; ".join(
            f"{e.title or e.url or e.id} [{e.type}/{e.status}]" for e in ev[:4]) + ("; ..." if len(ev) > 4 else "") + ".")
    basis = nd.selection_basis.value if nd.selection_basis else "unknown"
    parts.append(f"Policy score {score:.2f}" + (f" (selected, basis: {basis}"
                 f"{f', p={nd.probability:.2f}' if nd.probability is not None else ''})." if selected else "."))
    if reason:
        parts.append(f"No patch available: {reason}.")
    return " ".join(parts)


async def plan_candidates(
    ctx: PlanContext, nd: NormDecision, *, hyp: tuple[str, str] | None, llm: LLMClient | None,
    old_content_loader: OldContentLoader | None = None,
) -> list[Candidate]:
    """Pure planning (no DB): one Candidate per ActionType. LLM only touches the selected action."""
    confirmed = bool(hyp and hyp[1] == HypothesisStatus.CONFIRMED.value)
    ev = [e for e in ctx.evidence if e.type != EvidenceType.PROFOUND.value][:8] or ctx.evidence[:8]
    out: list[Candidate] = []
    for action in ActionType:
        tr = build_template(action, ctx)
        if old_content_loader and tr.change and tr.change.files:
            loaded = False
            for fc in tr.change.files:
                if fc.path not in ctx.old_contents:
                    try:
                        text = await old_content_loader(fc.path)
                    except Exception as exc:  # noqa: BLE001 - loader failure must not block proposing
                        log.warning("propose.old_content_failed", path=fc.path, error=str(exc))
                        text = None
                    if text is not None:
                        ctx.old_contents[fc.path] = text
                        loaded = True
            if loaded:
                tr = build_template(action, ctx)
        selected = action is nd.selected
        outcome = await generate_patch(action, ctx, tr, ctx.facts, llm if selected else None)
        change, reason = outcome.change, outcome.reason
        if change is not None:
            problems = validate_change(change)
            if problems:
                change, reason = None, "; ".join(problems[:2])
        score = nd.scores.get(action, 0.0)
        risk = assess_risk(action, root_cause_confirmed=confirmed, has_facts=bool(change and change.fact_ids)
                           or action is ActionType.OBSERVE, generated_by_llm=outcome.used_llm)
        out.append(Candidate(
            action=action, title=(change.title if change and change.title else _LABEL[action]),
            rationale=_rationale(action, hyp=hyp, nd=nd, selected=selected, ev=ev, reason=reason, score=score),
            risk=risk, score=score, selected=selected, change=change, unavailable_reason=reason,
            used_llm=outcome.used_llm))
    out.sort(key=lambda c: (-c.score, c.action.value))
    return out


async def _load_context(session: AsyncSession, incident: Any, evidence: list[Any] | None) -> tuple[Any, str | None,
                                                                    tuple[str, str, Any] | None, list[EvidenceItem]]:
    from app.models.core import Organization, PromptCluster

    org = await session.get(Organization, incident.org_id)
    topic = None
    if getattr(incident, "prompt_cluster_id", None):
        cluster = await session.get(PromptCluster, incident.prompt_cluster_id)
        topic = cluster.topic if cluster else None
    from app.models.evidence import Evidence, Hypothesis

    hyp = None
    rows = (await session.execute(select(Hypothesis).where(Hypothesis.incident_id == incident.id))).scalars().all()
    if rows:
        rows = sorted(rows, key=lambda h: (h.status != HypothesisStatus.CONFIRMED.value, -(h.confidence or 0.0)))
        hyp = (rows[0].title, str(rows[0].status), rows[0].id)
    if evidence is None:
        evidence = list((await session.execute(select(Evidence).where(Evidence.incident_id == incident.id)
                                               .order_by(Evidence.created_at))).scalars())
    return org, topic, hyp, [EvidenceItem.from_any(e) for e in evidence]


def build_plan_context(incident: Any, org: Any, capability: str, items: list[EvidenceItem], *,
                       canonical_facts: list[Any] | None = None, content_root: str | None = None,
                       extension: str | None = None, path_resolver: Callable[[str], str] | None = None) -> PlanContext:
    from app.core.config import get_settings

    inc_ctx = _get(incident, "context", default={}) or {}
    s = get_settings()
    owned = [e for e in items if e.type == EvidenceType.OWNED.value and e.url]
    live_owned = [e for e in owned if e.status in (EvidenceStatus.LIVE.value, EvidenceStatus.CHANGED.value)]
    target = inc_ctx.get("target_url") or next((e.url for e in live_owned + owned), None)
    facts = build_fact_base(items, canonical_facts if canonical_facts is not None else inc_ctx.get("canonical_facts"))
    return PlanContext(
        incident_id=str(incident.id), incident_number=getattr(incident, "number", None),
        incident_title=incident.title, org_name=_get(org, "name", default="the organization"),
        org_domain=_get(org, "domain"), capability=capability, facts=facts, evidence=items, target_url=target,
        repo_path=inc_ctx.get("repo_path"),
        content_root=content_root if content_root is not None else getattr(s, "github_content_root", ""),
        extension=extension or getattr(s, "github_content_extension", ".md"), path_resolver=path_resolver)


async def _resolve_version_id(session: AsyncSession, nd: NormDecision) -> uuid.UUID | None:
    if nd.policy_version_id or not nd.policy_version:
        return nd.policy_version_id
    from app.models.policy import PolicyVersion

    return (await session.execute(select(PolicyVersion.id).where(PolicyVersion.version == nd.policy_version))
            ).scalar_one_or_none()


async def _is_decided(session: AsyncSession, intervention_id: uuid.UUID) -> bool:
    q = select(Approval.id).where(Approval.intervention_id == intervention_id, Approval.status != "pending").limit(1)
    return (await session.execute(q)).first() is not None


async def propose_interventions(
    session: AsyncSession, incident: Any, decision: Any, *, evidence: list[Any] | None = None,
    llm: LLMClient | None = None, use_default_llm: bool = True, canonical_facts: list[Any] | None = None,
    old_content_loader: OldContentLoader | None = None, content_root: str | None = None,
    extension: str | None = None, path_resolver: Callable[[str], str] | None = None,
) -> list[Intervention]:
    """Create (or refresh undecided) candidate Interventions for ALL ActionType values; flushes, does not commit."""
    nd = normalize_decision(decision)
    org, topic, hyp, items = await _load_context(session, incident, evidence)
    capability = _derive_capability(incident, topic, hyp[0] if hyp else None)
    ctx = build_plan_context(incident, org, capability, items, canonical_facts=canonical_facts,
                             content_root=content_root, extension=extension, path_resolver=path_resolver)
    if llm is None and use_default_llm:
        llm = default_llm_client()
    cands = await plan_candidates(ctx, nd, hyp=hyp and (hyp[0], hyp[1]), llm=llm, old_content_loader=old_content_loader)
    version_id = await _resolve_version_id(session, nd)
    existing = {
        i.action: i for i in (await session.execute(select(Intervention).where(Intervention.incident_id == incident.id))
                              ).scalars()
    }
    rows: list[Intervention] = []
    for c in cands:
        payload = c.change.to_json() if c.change else None
        row = existing.get(c.action)
        if row is not None and await _is_decided(session, row.id):
            rows.append(row)  # a human already decided on this candidate: never rewrite it
            continue
        if row is None:
            row = Intervention(incident_id=incident.id, action=c.action)
            session.add(row)
        row.hypothesis_id = hyp[2] if hyp else None
        row.title, row.rationale, row.risk = c.title[:512], c.rationale, c.risk
        row.proposed_change, row.score, row.selected = payload, c.score, c.selected
        row.selection_basis = nd.selection_basis if c.selected else row.selection_basis or nd.selection_basis
        row.policy_version_id = version_id
        rows.append(row)
    await session.flush()
    log.info("interventions.proposed", incident=str(incident.id), n=len(rows), selected=nd.selected.value,
             slug=slugify(incident.title))
    return rows
