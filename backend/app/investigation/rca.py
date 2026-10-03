"""Root-cause hypothesis generation, organised by layer (AI engine -> citation -> owned -> competitor ->
external web -> canonical truth).

Deterministic rules map evidence patterns to hypotheses first. An optional LLM may then reason OVER the supplied
evidence only; its output must be JSON that validates against `LLMOutput`, may only cite evidence ids that were
supplied, and is otherwise discarded. With no LLM (or any LLM failure) the rules alone are returned.

Every hypothesis is `proposed`: it is a candidate explanation, never a fact. Confirmation is the evidence gate's
job (`app.investigation.evidence_gate`). Confidence here is a heuristic score built from the matched evidence,
capped at 0.9, and is not a calibrated probability.

Evidence is read by duck typing (ORM `Evidence` rows, dicts, or anything with the same attributes) via
`coerce_evidence`. Rules key on `type`, `status`, the ranker scores and these optional `raw` hints:
  profound: raw["citation_change"] in {added, lost, replaced}; raw["prompt_change"]; raw["model_update"];
            raw["kind"] == "query_fanout_shift" (material fanout movement only)
  competitor: raw["changed"] / raw["is_new"] (or status "changed")
  owned: raw["buried"], raw["click_depth"] (>=3 means buried), raw["last_modified"]
  external: raw["cited_by_ai"]
Missing hints never cause a rule to fire; unavailable/failed evidence is never interpreted as content.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.connectors.llm.gateway import ModelPurpose, ModelRequest, ModelResponse
from app.connectors.llm.prompts import neutralize as _neutralize
from app.connectors.llm.registry import get_prompt
from app.domain.enums import EvidenceStatus, EvidenceType, HypothesisStatus, IncidentCategory

log = structlog.get_logger(__name__)


class Layer(StrEnum):
    AI_ENGINE = "ai_engine"
    CITATION = "citation"
    OWNED_CONTENT = "owned_content"
    COMPETITOR = "competitor"
    EXTERNAL_WEB = "external_web"
    CANONICAL_TRUTH = "canonical_truth"
    UNDETERMINED = "undetermined"  # fallback: no actionable cause established


LAYER_ORDER: tuple[Layer, ...] = (
    Layer.AI_ENGINE, Layer.CITATION, Layer.OWNED_CONTENT, Layer.COMPETITOR, Layer.EXTERNAL_WEB,
    Layer.CANONICAL_TRUTH, Layer.UNDETERMINED,
)


# ---------------------------------------------------------------------------------------------------------
# Output / input models


class HypothesisDraft(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    rule_id: str
    layer: Layer
    title: str = Field(min_length=1)
    summary: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    produced_by: str = "rules"  # "rules:<rule_id>" or "llm:<label>"
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    actionable: bool = True  # False for the "no actionable cause established" fallback

    @field_validator("status")
    @classmethod
    def _always_proposed(cls, v: HypothesisStatus) -> HypothesisStatus:
        if v is not HypothesisStatus.PROPOSED:
            raise ValueError("RCA only produces proposed hypotheses")
        return v


class EvidenceView(BaseModel):
    id: str
    type: str = ""
    status: str = ""
    title: str = ""
    source: str = ""
    url: str | None = None
    excerpt: str = ""
    support: float | None = None
    contradiction: float | None = None
    freshness_risk: float | None = None
    confidence: float | None = None
    observed_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status not in (EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_str(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def coerce_evidence(obj: Any) -> EvidenceView:
    if isinstance(obj, EvidenceView):
        return obj
    raw = _get(obj, "raw") or {}
    return EvidenceView(
        id=str(_get(obj, "id")),
        type=_enum_str(_get(obj, "type")),
        status=_enum_str(_get(obj, "status")),
        title=str(_get(obj, "title") or ""),
        source=str(_get(obj, "source") or ""),
        url=_get(obj, "url"),
        excerpt=str(_get(obj, "excerpt") or ""),
        support=_num(_get(obj, "support_score")),
        contradiction=_num(_get(obj, "contradiction_score")),
        freshness_risk=_num(_get(obj, "freshness_risk")),
        confidence=_num(_get(obj, "confidence")),
        observed_at=_get(obj, "observed_at"),
        raw=dict(raw) if isinstance(raw, Mapping) else {},
    )


@dataclass(frozen=True)
class RCAConfig:
    actionable_threshold: float = 0.5  # below this the "no actionable cause" fallback is added
    max_confidence: float = 0.9
    stale_freshness_risk: float = 0.6
    contradiction_min: float = 0.5
    support_min: float = 0.5
    buried_click_depth: int = 3
    buried_url_depth: int = 4
    llm_confidence_cap: float = 0.8
    llm_max_hypotheses: int = 5
    llm_excerpt_chars: int = 400
    llm_max_evidence_items: int = 15


# ---------------------------------------------------------------------------------------------------------
# Rules


def _cat(incident: Any) -> str:
    return _enum_str(_get(incident, "category"))


def _mean(vals: Iterable[float | None], default: float = 0.5) -> float:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else default


def _clamp(x: float, cfg: RCAConfig) -> float:
    return round(max(0.05, min(cfg.max_confidence, x)), 3)


def _titles(items: Sequence[EvidenceView], n: int = 3) -> str:
    names = [i.title or i.url or i.source or i.id for i in items[:n]]
    extra = f" (+{len(items) - n} more)" if len(items) > n else ""
    return "; ".join(names) + extra


def _unavailable_penalty(evs: Sequence[EvidenceView]) -> float:
    if not evs:
        return 0.0
    return 0.1 if sum(1 for e in evs if not e.usable) / len(evs) > 0.5 else 0.0


@dataclass
class _Ctx:
    incident: Any
    ev: list[EvidenceView]
    cfg: RCAConfig

    def of_type(self, *types: EvidenceType) -> list[EvidenceView]:
        want = {t.value for t in types}
        return [e for e in self.ev if e.type in want and e.usable]

    @property
    def category(self) -> str:
        return _cat(self.incident)


def _rule_competitor_content(c: _Ctx) -> HypothesisDraft | None:
    hits = [
        e for e in c.of_type(EvidenceType.COMPETITOR)
        if e.status == EvidenceStatus.CHANGED.value or e.raw.get("changed") or e.raw.get("is_new")
    ]
    if not hits:
        return None
    cited = [e for e in c.of_type(EvidenceType.PROFOUND) if e.raw.get("citation_change") in ("added", "replaced")]
    conf = 0.45 + 0.25 * _mean(e.confidence for e in hits)
    if c.category in (IncidentCategory.COMPETITOR_CITATION_GAIN.value, IncidentCategory.NEW_COMPETITOR_CONTENT.value,
                      IncidentCategory.VISIBILITY_DROP.value):
        conf += 0.1
    if cited:
        conf += 0.1
    ids = [e.id for e in hits] + [e.id for e in cited]
    return HypothesisDraft(
        rule_id="competitor_canonical_improved", layer=Layer.COMPETITOR,
        title="A competitor published or improved dedicated content on this topic",
        summary="Competitor pages relevant to the affected prompts are new or changed, which can displace our pages "
                "as the source AI engines cite.",
        confidence=_clamp(conf - _unavailable_penalty(hits), c.cfg), evidence_ids=ids,
        rationale=f"{len(hits)} competitor evidence item(s) show new or changed content: {_titles(hits)}."
                  + (f" {len(cited)} Profound citation record(s) show newly added sources." if cited else ""),
        missing_evidence=[] if cited else ["Profound citation data showing the competitor page is now cited"],
        produced_by="rules:competitor_canonical_improved",
    )


def _rule_query_interpretation(c: _Ctx) -> HypothesisDraft | None:
    hits = [e for e in c.of_type(EvidenceType.PROFOUND) if e.raw.get("kind") == "query_fanout_shift"]
    if not hits:
        return None
    emerged = [e for e in hits if e.raw.get("shift") == "competitor_emerged"]
    owned_changed = [
        e for e in c.of_type(EvidenceType.OWNED)
        if e.status == EvidenceStatus.CHANGED.value or e.raw.get("changed")
    ]
    conf = 0.35 + (0.25 if emerged else 0.0)
    if c.category in (IncidentCategory.VISIBILITY_DROP.value, IncidentCategory.COMPETITOR_CITATION_GAIN.value):
        conf += 0.1
    if owned_changed:
        conf -= 0.15
    return HypothesisDraft(
        rule_id="query_interpretation_shifted", layer=Layer.AI_ENGINE,
        title="Answer engines changed which searches they run for these prompts",
        summary="Tracked prompts now fan out into materially different queries. That can explain a competitor "
                "appearing without any change to our own page.",
        confidence=_clamp(conf, c.cfg),
        evidence_ids=[e.id for e in hits],
        contradicting_evidence_ids=[e.id for e in owned_changed],
        rationale=(
            f"{len(hits)} material query-fanout change(s), {len(emerged)} naming a competitor: {_titles(hits)}."
            + (f" Counterevidence: {len(owned_changed)} owned page(s) also changed, so the shift is not the only explanation."
               if owned_changed else " No owned-page change was supplied as counterevidence.")
        ),
        missing_evidence=[] if emerged else ["A fanout query that newly names a competitor at a material share"],
        produced_by="rules:query_interpretation_shifted",
    )


def _rule_citation_changed(c: _Ctx) -> HypothesisDraft | None:
    hits = [
        e for e in c.of_type(EvidenceType.PROFOUND)
        if e.raw.get("citation_change") in ("added", "lost", "replaced")
        or (e.status == EvidenceStatus.CHANGED.value and e.raw.get("citation_change") is not False)
    ]
    if not hits:
        return None
    conf = 0.45 + 0.25 * _mean(e.confidence for e in hits)
    if c.category == IncidentCategory.LOST_CITATION_SOURCE.value:
        conf += 0.15
    elif c.category in (IncidentCategory.VISIBILITY_DROP.value, IncidentCategory.COMPETITOR_CITATION_GAIN.value):
        conf += 0.05
    lost = [e for e in hits if e.raw.get("citation_change") == "lost"]
    return HypothesisDraft(
        rule_id="citation_source_changed", layer=Layer.CITATION,
        title="The set of sources AI engines cite for these prompts changed",
        summary="Sources cited in answers were added, lost or replaced, changing which brands the engines surface.",
        confidence=_clamp(conf, c.cfg), evidence_ids=[e.id for e in hits],
        rationale=f"{len(hits)} Profound citation record(s) show changes ({len(lost)} lost): {_titles(hits)}.",
        missing_evidence=[] if lost or c.of_type(EvidenceType.COMPETITOR) else ["What now occupies the lost citation slots"],
        produced_by="rules:citation_source_changed",
    )


def _rule_owned_stale(c: _Ctx) -> HypothesisDraft | None:
    hits = [
        e for e in c.of_type(EvidenceType.OWNED)
        if e.status == EvidenceStatus.STALE.value or (e.freshness_risk or 0) >= c.cfg.stale_freshness_risk
    ]
    if not hits:
        return None
    conf = 0.5 + 0.2 * _mean(e.freshness_risk for e in hits)
    if c.category in (IncidentCategory.STALE_INFORMATION.value, IncidentCategory.FACTUAL_CONFLICT.value):
        conf += 0.1
    return HypothesisDraft(
        rule_id="owned_content_stale", layer=Layer.OWNED_CONTENT,
        title="Our own content on this topic is stale or outdated",
        summary="Owned pages that AI engines can cite appear out of date relative to the current product or facts.",
        confidence=_clamp(conf, c.cfg), evidence_ids=[e.id for e in hits],
        rationale=f"{len(hits)} owned page(s) are marked stale or carry high freshness risk: {_titles(hits)}.",
        missing_evidence=[] if c.of_type(EvidenceType.PROFOUND) else ["Profound data showing these pages are the ones cited"],
        produced_by="rules:owned_content_stale",
    )


def _is_buried(e: EvidenceView, cfg: RCAConfig) -> bool:
    if e.raw.get("buried") is True:
        return True
    depth = e.raw.get("click_depth")
    if isinstance(depth, (int, float)) and depth >= cfg.buried_click_depth:
        return True
    if e.url:
        segs = [s for s in urlparse(e.url).path.split("/") if s]
        return len(segs) >= cfg.buried_url_depth
    return False


def _rule_buried(c: _Ctx) -> HypothesisDraft | None:
    hits = [
        e for e in c.of_type(EvidenceType.OWNED)
        if e.status != EvidenceStatus.STALE.value and (e.support or 0) >= c.cfg.support_min and _is_buried(e, c.cfg)
    ]
    if not hits:
        return None
    conf = 0.4 + 0.25 * _mean(e.support for e in hits)
    if c.of_type(EvidenceType.COMPETITOR):
        conf += 0.1
    if c.category == IncidentCategory.VISIBILITY_DROP.value:
        conf += 0.05
    return HypothesisDraft(
        rule_id="capability_exists_but_buried", layer=Layer.OWNED_CONTENT,
        title="The capability exists in our content but is buried and hard for engines to find",
        summary="An owned page supports the claim, but it sits deep in the site structure rather than on a dedicated, "
                "easily discovered canonical page.",
        confidence=_clamp(conf, c.cfg), evidence_ids=[e.id for e in hits],
        rationale=f"{len(hits)} owned page(s) support the claim yet are deeply nested: {_titles(hits)}.",
        missing_evidence=["Proof that no shallower dedicated page exists (site-wide crawl)"],
        produced_by="rules:capability_exists_but_buried",
    )


def _rule_external_obsolete(c: _Ctx) -> HypothesisDraft | None:
    hits = [
        e for e in c.of_type(EvidenceType.EXTERNAL)
        if (e.contradiction or 0) >= c.cfg.contradiction_min
        or e.status == EvidenceStatus.STALE.value
        or (e.freshness_risk or 0) >= c.cfg.stale_freshness_risk
    ]
    if not hits:
        return None
    conf = 0.3 + 0.25 * _mean(max(e.contradiction or 0, e.freshness_risk or 0) for e in hits)
    if any(e.raw.get("cited_by_ai") for e in hits):
        conf += 0.15
    if c.category in (IncidentCategory.FACTUAL_CONFLICT.value, IncidentCategory.STALE_INFORMATION.value):
        conf += 0.1
    return HypothesisDraft(
        rule_id="obsolete_third_party_info", layer=Layer.EXTERNAL_WEB,
        title="Third-party sources carry outdated or contradicting information",
        summary="External pages that engines may rely on conflict with our current facts or are out of date.",
        confidence=_clamp(conf, c.cfg), evidence_ids=[e.id for e in hits],
        rationale=f"{len(hits)} external source(s) contradict current facts or look stale: {_titles(hits)}.",
        missing_evidence=[] if any(e.raw.get("cited_by_ai") for e in hits) else ["Confirmation that AI answers cite these sources"],
        produced_by="rules:obsolete_third_party_info",
    )


def _rule_canonical_truth(c: _Ctx) -> HypothesisDraft | None:
    truth = [
        e for e in c.of_type(EvidenceType.OWNED)
        if e.status == EvidenceStatus.LIVE.value and (e.support or 0) >= c.cfg.support_min
    ]
    conflicting = [
        e for e in c.ev
        if e.usable and e.type != EvidenceType.OWNED.value and (e.contradiction or 0) >= c.cfg.contradiction_min
    ]
    if not truth or not conflicting:
        return None
    conf = 0.45 + 0.2 * _mean(e.support for e in truth) + 0.1 * _mean(e.contradiction for e in conflicting)
    if c.category == IncidentCategory.FACTUAL_CONFLICT.value:
        conf += 0.1
    return HypothesisDraft(
        rule_id="canonical_truth_conflicts_with_web", layer=Layer.CANONICAL_TRUTH,
        title="Our canonical content states the correct facts; other sources contradict it",
        summary="The live owned source supports the claim while other sources contradict it, so the conflict is "
                "outside our canonical content.",
        confidence=_clamp(conf, c.cfg), evidence_ids=[e.id for e in truth] + [e.id for e in conflicting],
        contradicting_evidence_ids=[e.id for e in conflicting],
        rationale=f"Live owned evidence supports the claim ({_titles(truth)}); contradicted by {_titles(conflicting)}.",
        missing_evidence=["Evidence that AI answers actually reproduce the contradicting claim"],
        produced_by="rules:canonical_truth_conflicts_with_web",
    )


def _rule_prompt_cluster_changed(c: _Ctx) -> HypothesisDraft | None:
    hits = [e for e in c.of_type(EvidenceType.PROFOUND) if e.raw.get("prompt_change") or e.raw.get("model_update")]
    if not hits:  # the incident category alone is a detector observation, not evidence of a cause
        return None
    model = [e for e in hits if e.raw.get("model_update")]
    conf = 0.45 + 0.2 * _mean(e.confidence for e in hits)
    if c.category == IncidentCategory.PROMPT_VOLUME_SPIKE.value:
        conf += 0.15
    title = (
        "The AI engine's behaviour changed (model update)" if model and len(model) == len(hits)
        else "The prompt cluster itself changed (new prompts or shifted volume), not our content"
    )
    return HypothesisDraft(
        rule_id="prompt_cluster_changed", layer=Layer.AI_ENGINE, title=title,
        summary="The observed movement is explained by a change in which prompts are asked or how engines answer them.",
        confidence=_clamp(conf, c.cfg), evidence_ids=[e.id for e in hits],
        rationale=f"{len(hits)} Profound record(s) report prompt-set or engine changes: {_titles(hits)}.",
        missing_evidence=["Comparison of the same prompts across engines before and after the change"],
        produced_by="rules:prompt_cluster_changed",
    )


RULES: tuple[Callable[[_Ctx], HypothesisDraft | None], ...] = (
    _rule_prompt_cluster_changed, _rule_query_interpretation, _rule_citation_changed, _rule_owned_stale, _rule_buried,
    _rule_competitor_content, _rule_external_obsolete, _rule_canonical_truth,
)


def _no_cause(c: _Ctx, best: float) -> HypothesisDraft:
    unusable = [e for e in c.ev if not e.usable]
    return HypothesisDraft(
        rule_id="no_actionable_cause", layer=Layer.UNDETERMINED,
        title="No actionable root cause established from the available evidence",
        summary="Evidence is missing, unavailable or inconclusive. Recommend observe or human investigation.",
        confidence=round(max(0.05, min(c.cfg.max_confidence, 0.5 + 0.4 * (1 - best))), 3),
        evidence_ids=[e.id for e in unusable],
        rationale=(
            f"{len(c.ev)} evidence item(s) supplied; best rule-based hypothesis confidence {best:.2f} is below "
            f"the {c.cfg.actionable_threshold:.2f} actionable threshold; {len(unusable)} item(s) unavailable "
            "(not inferred). Confidence here means confidence that evidence is insufficient."
        ),
        missing_evidence=["Collected evidence for each layer (citations, owned page, competitor page, external sources)"],
        produced_by="rules:no_actionable_cause", actionable=False,
    )


# ---------------------------------------------------------------------------------------------------------
# LLM path


@runtime_checkable
class LLMClient(Protocol):
    """Minimal structured-output client. Implementations may be sync or async (see `agenerate_hypotheses`)."""

    def complete_json(self, *, system: str, prompt: str, schema: dict[str, Any]) -> str | dict[str, Any]: ...


class LLMHypothesis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layer: Layer
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(default="", max_length=1500)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)
    rationale: str = Field(default="", max_length=2000)


class LLMOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hypotheses: list[LLMHypothesis]


_PROMPT = get_prompt("hypothesis_generator_v3")
PROMPT_VERSION = _PROMPT.name
SYSTEM_PROMPT = _PROMPT.system.replace("{layers}", ", ".join(layer.value for layer in Layer if layer is not Layer.UNDETERMINED))


_EVIDENCE_TAG = re.compile(r"<\s*/?\s*evidence", re.IGNORECASE)


def neutralize(text: str) -> str:
    """Untrusted text may not open/close our <evidence> or <untrusted_data> delimiters."""
    return _EVIDENCE_TAG.sub(lambda m: m.group(0).replace("<", "[", 1), _neutralize(str(text)))


def build_llm_prompt(incident: Any, evidence: Sequence[EvidenceView], cfg: RCAConfig) -> str:
    lines = [
        f"Incident: {_get(incident, 'title', '')}",
        f"Category: {_cat(incident)}",
        f"Summary: {_get(incident, 'summary', '')}",
        f"Metrics: {json.dumps(_get(incident, 'metrics', []), default=str)[:1500]}",
        "Evidence:",
    ]
    for e in evidence:
        scores = {k: v for k, v in (("support", e.support), ("contradiction", e.contradiction),
                                     ("freshness_risk", e.freshness_risk)) if v is not None}
        lines.append(
            f'<evidence id="{e.id}" type="{e.type}" status="{e.status}" scores="{json.dumps(scores)}">'
            f"{neutralize(e.title)} | {neutralize(str(e.url or e.source))} | "
            f"{neutralize(e.excerpt[: cfg.llm_excerpt_chars])}</evidence>"
        )
    return "\n".join(lines)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_llm_output(payload: str | Mapping[str, Any] | LLMOutput | None) -> LLMOutput | None:
    """Parse + validate. Returns None for anything that is not a valid `LLMOutput`."""
    if payload is None:
        return None
    if isinstance(payload, LLMOutput):
        return payload
    try:
        data = json.loads(_FENCE.sub("", payload.strip())) if isinstance(payload, str) else dict(payload)
        return LLMOutput.model_validate(data)
    except (ValueError, TypeError, ValidationError):
        return None


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def _norm_url(u: str) -> str:
    return str(u).strip().rstrip("/.,;").lower()


def _scrub_urls(text: str, known: set[str]) -> str:
    return _URL_RE.sub(lambda m: m.group(0) if _norm_url(m.group(0)) in known else "[unverified url removed]", text)


def _accept_llm(out: LLMOutput, ev: Sequence[EvidenceView], existing: list[HypothesisDraft], cfg: RCAConfig,
                label: str) -> tuple[list[HypothesisDraft], list[str]]:
    known = {e.id for e in ev}
    known_urls = {_norm_url(u) for e in ev for u in (e.url, e.source) if u}
    seen = {(h.layer, frozenset(h.evidence_ids)) for h in existing}
    accepted: list[HypothesisDraft] = []
    warnings: list[str] = []
    for h in out.hypotheses[: cfg.llm_max_hypotheses]:
        ids = list(dict.fromkeys(h.evidence_ids))
        unknown = [i for i in ids if i not in known]
        if unknown:
            warnings.append(f"llm hypothesis '{h.title[:60]}' discarded: unknown evidence ids {unknown}")
            continue
        if h.layer is Layer.UNDETERMINED:
            warnings.append("llm hypothesis with undetermined layer discarded")
            continue
        key = (h.layer, frozenset(ids))
        if key in seen:
            continue
        seen.add(key)
        invented = [u for u in _URL_RE.findall(f"{h.title} {h.summary} {h.rationale}") if _norm_url(u) not in known_urls]
        if invented:  # a URL the fetch subsystem never retrieved must not become evidence or a citation
            warnings.append(f"llm hypothesis '{h.title[:60]}': {len(invented)} unretrieved url(s) removed")
            h = h.model_copy(update={f: _scrub_urls(getattr(h, f), known_urls) for f in ("title", "summary", "rationale")})
        accepted.append(HypothesisDraft(
            rule_id="llm", layer=h.layer, title=h.title, summary=h.summary,
            confidence=round(min(h.confidence, cfg.llm_confidence_cap), 3), evidence_ids=ids,
            rationale=h.rationale, produced_by=f"llm:{label}",
        ))
    return accepted, warnings


def compute_rca_complexity(incident: Any, ev: Sequence[EvidenceView], rules: Sequence[HypothesisDraft]) -> float:
    """Computes a deterministic complexity score in [0.0, 1.0].

    Higher score indicates complex, conflicting, or multi-faceted incident evidence
    that may justify escalation to the DEEP tier.
    """
    score = 0.0
    usable_ev = [e for e in ev if e.usable]

    # 1. Evidence volume: > 8 items (+0.15), > 15 items (+0.15)
    n_ev = len(usable_ev)
    if n_ev > 15:
        score += 0.30
    elif n_ev > 8:
        score += 0.15

    # 2. Evidence families (diversity of sources / types)
    families = {e.type for e in usable_ev if e.type}
    if len(families) >= 4:
        score += 0.20
    elif len(families) >= 3:
        score += 0.10

    # 3. Competing plausible rule-based hypotheses
    actionable_rules = [r for r in rules if r.actionable and r.confidence >= 0.5]
    if len(actionable_rules) >= 3:
        score += 0.20
    elif len(actionable_rules) >= 2:
        score += 0.10

    # 4. Conflicting evidence or counterevidence
    has_contradiction = any((e.contradiction or 0) > 0.4 for e in usable_ev) or any(
        bool(r.contradicting_evidence_ids) for r in rules
    )
    if has_contradiction:
        score += 0.20

    # 5. Query fanout shift / multi-platform / competitor shift hints
    has_fanout = any(e.raw.get("kind") == "query_fanout_shift" for e in usable_ev)
    competitors = {e.raw.get("competitor") for e in usable_ev if e.raw.get("competitor")}
    if len(competitors) > 1 or (has_fanout and len(competitors) >= 1):
        score += 0.15

    return min(round(score, 2), 1.0)


def _llm_request(incident: Any, ev: Sequence[EvidenceView], cfg: RCAConfig, complexity_score: float | None = None) -> ModelRequest:
    # Token discipline: prioritize evidence by relevance score and cap at llm_max_evidence_items
    sorted_ev = sorted(ev, key=lambda e: (-(e.support or 0.0), e.freshness_risk or 0.0))[: cfg.llm_max_evidence_items]
    return ModelRequest(
        purpose=ModelPurpose.HYPOTHESIS_GENERATION, system=SYSTEM_PROMPT, input=build_llm_prompt(incident, sorted_ev, cfg),
        prompt_version=PROMPT_VERSION, response_schema=LLMOutput, complexity_score=complexity_score,
        metadata={"incident_id": str(_get(incident, "id", "")), "evidence_count": len(ev),
                  "sent_evidence_count": len(sorted_ev), "complexity_score": complexity_score},
    )


def _call_llm(llm: Any, incident: Any, ev: Sequence[EvidenceView], cfg: RCAConfig, complexity_score: float | None = None) -> Any:
    """ModelGateway (generate_structured) when available; legacy `complete_json` clients (tests, older callers) too."""
    req = _llm_request(incident, ev, cfg, complexity_score=complexity_score)
    if hasattr(llm, "generate_structured"):
        return llm.generate_structured(req, LLMOutput)
    return llm.complete_json(
        system=SYSTEM_PROMPT, prompt=req.input or build_llm_prompt(incident, ev, cfg), schema=LLMOutput.model_json_schema()
    )


@dataclass
class RCAResult:
    hypotheses: list[HypothesisDraft]
    warnings: list[str]
    llm_used: bool
    llm_calls: list[dict[str, Any]] = field(default_factory=list)  # ModelCallMeta dicts: no prompt text, no reasoning
    complexity_score: float = 0.0


def _rules_only(incident: Any, ev: list[EvidenceView], cfg: RCAConfig) -> list[HypothesisDraft]:
    ctx = _Ctx(incident, ev, cfg)
    out = [h for rule in RULES if (h := rule(ctx)) is not None]
    return out


def _finalise(rules: list[HypothesisDraft], llm: list[HypothesisDraft], incident: Any, ev: list[EvidenceView],
              cfg: RCAConfig) -> list[HypothesisDraft]:
    hyps = rules + llm
    best = max((h.confidence for h in hyps), default=0.0)
    if best < cfg.actionable_threshold:
        hyps.append(_no_cause(_Ctx(incident, ev, cfg), best))
    hyps.sort(key=lambda h: (-h.confidence if h.actionable else 1, LAYER_ORDER.index(h.layer)))
    return hyps


def run_rca(incident: Any, evidence: Iterable[Any], llm: LLMClient | None = None,
            config: RCAConfig | None = None) -> RCAResult:
    cfg = config or RCAConfig()
    ev = [coerce_evidence(e) for e in evidence]
    rules = _rules_only(incident, ev, cfg)
    complexity = compute_rca_complexity(incident, ev, rules)
    llm_h: list[HypothesisDraft] = []
    warnings: list[str] = []
    calls: list[dict[str, Any]] = []
    used = False
    if llm is not None and ev:
        try:
            payload = _call_llm(llm, incident, ev, cfg, complexity_score=complexity)
            if inspect.isawaitable(payload):
                if inspect.iscoroutine(payload):
                    payload.close()
                payload = None
                warnings.append("llm client is async; use agenerate_hypotheses. LLM output discarded")
            else:
                calls += _meta_of(payload)
                used, llm_h, w = _consume(payload, ev, rules, cfg, llm)
                warnings += w
        except Exception as exc:  # noqa: BLE001 - LLM unavailable must never break RCA
            log.warning("rca.llm_failed", error=str(exc))
            warnings.append(f"llm unavailable: {type(exc).__name__}")
    return RCAResult(_finalise(rules, llm_h, incident, ev, cfg), warnings, used, calls, complexity_score=complexity)


async def arun_rca(incident: Any, evidence: Iterable[Any], llm: Any = None,
                   config: RCAConfig | None = None) -> RCAResult:
    """Async variant: the LLM client's `complete_json` may be a coroutine function."""
    cfg = config or RCAConfig()
    ev = [coerce_evidence(e) for e in evidence]
    rules = _rules_only(incident, ev, cfg)
    complexity = compute_rca_complexity(incident, ev, rules)
    llm_h: list[HypothesisDraft] = []
    warnings: list[str] = []
    calls: list[dict[str, Any]] = []
    used = False
    if llm is not None and ev:
        try:
            payload = _call_llm(llm, incident, ev, cfg, complexity_score=complexity)
            if inspect.isawaitable(payload):
                payload = await payload
            calls += _meta_of(payload)
            used, llm_h, w = _consume(payload, ev, rules, cfg, llm)
            warnings += w
        except Exception as exc:  # noqa: BLE001
            log.warning("rca.llm_failed", error=str(exc))
            warnings.append(f"llm unavailable: {type(exc).__name__}")
    return RCAResult(_finalise(rules, llm_h, incident, ev, cfg), warnings, used, calls, complexity_score=complexity)


def _meta_of(payload: Any) -> list[dict[str, Any]]:
    return [payload.meta.as_dict()] if isinstance(payload, ModelResponse) else []


def _consume(payload: Any, ev: list[EvidenceView], rules: list[HypothesisDraft], cfg: RCAConfig,
             llm: Any) -> tuple[bool, list[HypothesisDraft], list[str]]:
    version = ""
    if isinstance(payload, ModelResponse):
        version, payload = payload.meta.prompt_version, payload.parsed
    parsed = parse_llm_output(payload)
    if parsed is None:
        return False, [], ["llm output failed schema validation; discarded"]
    label = str(getattr(llm, "name", None) or type(llm).__name__) + (f"@{version}" if version else "")
    accepted, warnings = _accept_llm(parsed, ev, rules, cfg, label)
    return True, accepted, warnings


def generate_hypotheses(incident: Any, evidence: Iterable[Any], llm: LLMClient | None = None) -> list[HypothesisDraft]:
    """Deterministic rules first; optional LLM reasoning over the supplied evidence. All results are `proposed`."""
    return run_rca(incident, evidence, llm).hypotheses


async def agenerate_hypotheses(incident: Any, evidence: Iterable[Any], llm: Any = None) -> list[HypothesisDraft]:
    return (await arun_rca(incident, evidence, llm)).hypotheses


__all__ = [
    "EvidenceView",
    "HypothesisDraft",
    "LLMClient",
    "LLMOutput",
    "Layer",
    "RCAConfig",
    "RCAResult",
    "agenerate_hypotheses",
    "arun_rca",
    "coerce_evidence",
    "compute_rca_complexity",
    "generate_hypotheses",
    "parse_llm_output",
    "run_rca",
]
