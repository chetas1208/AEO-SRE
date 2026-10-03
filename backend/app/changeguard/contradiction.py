"""Canonical-truth contradiction check (Change Guard check 3). Pure functions; no DB access.

Layers, in precedence order:
 1. RULES (`compare_claims`): same subject -> plan-availability sets (inclusion / exclusivity / negation), numeric
    limits and prices, dated claims. Definitive when `needs_semantic` is False.
 2. EvidenceRanker (optional, ~62% 3-class accuracy, so it can only raise `canonical_uncertain`, never BLOCK-grade
    CONFLICTING and never clear a pair).
 3. ModelGateway NLI-style classification (optional, only when health is READY) over ONLY the ambiguous pairs.

Precedence (tested):
 * A deterministic CONFLICTING is final (semantic layers cannot downgrade it).
 * A definitive DUPLICATE/COMPATIBLE/DEPENDENT/UNRELATED (confidence >= HIGH_CONF, not ambiguous) is final: the model
   and ranker are NOT consulted and cannot raise it.
 * For ambiguous pairs: a model CONFLICTING becomes CONFLICTING only if the pair has lexical/entity overlap AND the
   (non-degraded) ranker does not say support >= RANKER_SUPPORT_HIGH; otherwise it is downgraded to `uncertain`
   (REQUIRE_REVIEW grade). A ranker contradiction can produce `uncertain` only. A model "compatible" never clears a
   ranker-raised concern. Unresolved ambiguous pairs (no semantic layer ran) are listed and force `degraded`.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, ValidationError

from app.changeguard.claims import (
    DEFAULT_TABLE,
    INF,
    NEGATE,
    Claim,
    Number,
    canonical_from_mapping,
    content_tokens,
    normalize_text,
)

log = structlog.get_logger(__name__)

DUPLICATE, COMPATIBLE, DEPENDENT, CONFLICTING, UNRELATED = (
    "DUPLICATE", "COMPATIBLE", "DEPENDENT", "CONFLICTING", "UNRELATED")
RELATIONS = (DUPLICATE, COMPATIBLE, DEPENDENT, CONFLICTING, UNRELATED)
HIGH_CONF = 0.8
RANKER_CONTRA = 0.55
RANKER_SUPPORT_HIGH = 0.75
MAX_PAIRS_PER_CALL = 10
MAX_ESCALATED_PAIRS = 60
MODEL_ATTEMPTS = 2
ANY = "*"  # sentinel: "somewhere / unspecified plan"
AVAIL_PREDS = ("offers", "integrates")


@dataclass(frozen=True)
class Relation:
    relation: str
    reasons: tuple[str, ...] = ()
    confidence: float = 0.9
    needs_semantic: bool = False  # rules could not decide; escalate

    def as_dict(self) -> dict[str, Any]:
        return {"relation": self.relation, "reasons": list(self.reasons), "confidence": self.confidence,
                "needs_semantic": self.needs_semantic}


# --------------------------------------------------------------------------- rules
def _universe(plans: Sequence[str]) -> list[str]:
    order = DEFAULT_TABLE.plan_order
    return list(dict.fromkeys([*order, *plans]))


def _avail(c: Claim) -> tuple[set[str], set[str]]:
    """(must_have, must_not) plan sets implied by an availability claim."""
    plans = set(c.plans)
    if c.polarity == NEGATE:
        return set(), (plans or {ANY})
    have = plans or {ANY}
    if c.exclusive:
        return have, set(_universe(c.plans)) - plans
    return have, set()


def _avail_conflict(have: set[str], not_: set[str]) -> set[str]:
    if ANY in not_:
        return {ANY} if have else set()
    return (have - {ANY}) & not_


def _interval(n: Number) -> tuple[float, float]:
    if n.kind == "max":
        return 0.0, n.value
    if n.kind == "min":
        return n.value, INF
    return n.value, n.value


def _num_conflict(a: Number, b: Number) -> bool:
    if a.unit != b.unit:
        return False
    if a.kind == b.kind and a.value != b.value:
        return True
    (alo, ahi), (blo, bhi) = _interval(a), _interval(b)
    return ahi < blo or bhi < alo


def _date_conflict(a: str, b: str) -> bool:
    n = min(len(a), len(b))
    return a[:n] != b[:n]


def _subjects_related(a: str, b: str) -> bool:
    return any({a, b} <= grp for grp in DEFAULT_TABLE.related)


def lexical_overlap(a: Claim, b: Claim) -> bool:
    ta, tb = content_tokens(a.text), content_tokens(b.text)
    shared = ta & tb
    if set(a.entities) & set(b.entities) - {""}:
        return True
    return len(shared) >= 2 or (bool(shared) and len(shared) / max(1, len(ta | tb)) >= 0.34)


def compare_claims(proposed: Claim, canonical: Claim) -> Relation:
    if normalize_text(proposed.text) == normalize_text(canonical.text):
        return Relation(DUPLICATE, ("identical_text",), 0.99)
    ps, cs = proposed.subject, canonical.subject
    if ps and cs and ps != cs:
        if _subjects_related(ps, cs):
            return Relation(UNRELATED, ("related_subjects",), 0.4, needs_semantic=True)
        return Relation(UNRELATED, ("different_subject",), 0.9)
    if not (ps and cs):
        if lexical_overlap(proposed, canonical):
            return Relation(UNRELATED, ("unparsed_with_overlap",), 0.4, needs_semantic=True)
        return Relation(UNRELATED, ("no_overlap",), 0.85)

    conflicts: list[tuple[str, float]] = []
    p_have, p_not = _avail(proposed) if proposed.predicate in AVAIL_PREDS else (set(), set())
    c_have, c_not = _avail(canonical) if canonical.predicate in AVAIL_PREDS else (set(), set())
    clash = _avail_conflict(p_have, c_not) | _avail_conflict(c_have, p_not)
    if clash:
        reason = ("exclusivity_vs_inclusion" if (proposed.exclusive or canonical.exclusive)
                  else "negation_mismatch")
        conflicts.append((reason, 0.95))
    if (proposed.not_exclusive and canonical.exclusive and set(proposed.plans) & set(canonical.plans)) or \
            (canonical.not_exclusive and proposed.exclusive and set(proposed.plans) & set(canonical.plans)):
        conflicts.append(("exclusivity_vs_inclusion", 0.9))

    scope_known = bool(proposed.plans) and bool(canonical.plans)
    plans_overlap = bool(set(proposed.plans) & set(canonical.plans))
    num_comparable = not scope_known or plans_overlap
    if num_comparable:
        for pn in proposed.numbers:
            for cn in canonical.numbers:
                if _num_conflict(pn, cn):
                    conflicts.append(("numeric_mismatch", 0.9 if scope_known else 0.75))
                    if not scope_known:
                        conflicts.append(("scope_mismatch", 0.75))
    for pd in proposed.dates:
        for cd in canonical.dates:
            if pd.role == cd.role and _date_conflict(pd.value, cd.value):
                conflicts.append(("date_mismatch", 0.9))
    if conflicts:
        reasons = tuple(dict.fromkeys(r for r, _ in conflicts))
        return Relation(CONFLICTING, reasons, max(c for _, c in conflicts))

    # ---- no conflict: duplicate / compatible / dependent
    if proposed.qualifiers.get("scope") != canonical.qualifiers.get("scope") and (
            "add_on" in (proposed.qualifiers.get("scope"), canonical.qualifiers.get("scope"))):
        return Relation(DEPENDENT, ("scope_mismatch", "add_on_qualifier"), 0.5, needs_semantic=True)
    reasons: list[str] = []
    same_pol = proposed.polarity == canonical.polarity
    new_info = False
    if proposed.predicate in AVAIL_PREDS and canonical.predicate in AVAIL_PREDS:
        if same_pol:
            if proposed.polarity == NEGATE:
                extra = set(proposed.plans) - set(canonical.plans) if canonical.plans else set()
                new_info = bool(extra) or (not proposed.plans and bool(canonical.plans))
            else:
                extra = (p_have - {ANY}) - (c_have - {ANY}) if canonical.plans else set()
                new_info = bool(extra) or (bool(proposed.plans) and not canonical.plans)
                if proposed.exclusive and not canonical.exclusive:
                    new_info = True
                    reasons.append("exclusivity_not_in_canonical")
        else:
            new_info = True  # opposite polarity on disjoint plans (e.g. negation of a plan canonical doesn't cover)
    # numbers/dates present only on one side
    comparable_n = {(n.unit) for n in canonical.numbers}
    for pn in proposed.numbers:
        if pn.unit not in comparable_n:
            new_info = True
    for pd in proposed.dates:
        if all(pd.role != cd.role for cd in canonical.dates):
            new_info = True
    if new_info:
        reasons.append("scope_mismatch")
        return Relation(DEPENDENT, tuple(dict.fromkeys(reasons)), 0.85)
    identical = (
        same_pol and proposed.exclusive == canonical.exclusive and set(proposed.plans) == set(canonical.plans)
        and set(proposed.numbers) == set(canonical.numbers)
        and {(d.role, d.value) for d in proposed.dates} == {(d.role, d.value) for d in canonical.dates}
    )
    if identical:
        return Relation(DUPLICATE, ("same_subject_predicate_scope",), 0.95)
    return Relation(COMPATIBLE, ("subset_of_canonical",), 0.9)


# --------------------------------------------------------------------------- results
@dataclass
class Finding:
    type: str  # canonical_conflict | canonical_uncertain
    severity: str  # high | medium
    proposed_claim_id: str
    canonical_claim_id: str
    relation: str
    reasons: list[str]
    confidence: float
    source: str = "rules"  # rules | ranker | model

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class EvaluationResult:
    findings: list[Finding] = field(default_factory=list)
    semantic_check: str = "ok"  # ok | degraded | skipped_no_canonical_truth
    pairs_compared: int = 0
    model_used: bool = False
    ranker_used: bool = False
    ranker_degraded: bool = False
    ignored_canonical: dict[str, int] = field(default_factory=dict)  # retired / not_yet_valid / expired
    unresolved_pairs: list[tuple[str, str]] = field(default_factory=list)
    degraded_reasons: list[str] = field(default_factory=list)
    pair_relations: list[dict[str, Any]] = field(default_factory=list)  # non-UNRELATED pairs, for audit

    def as_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k not in ("findings",)}
        d["findings"] = [f.as_dict() for f in self.findings]
        d["unresolved_pairs"] = [list(p) for p in self.unresolved_pairs]
        return d


# --------------------------------------------------------------------------- model layer
class PairVerdict(BaseModel):
    proposed_id: str = Field(max_length=200)
    canonical_id: str = Field(max_length=200)
    relation: Literal["DUPLICATE", "COMPATIBLE", "DEPENDENT", "CONFLICTING", "UNRELATED"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=400)


class ClassifierOutput(BaseModel):
    verdicts: list[PairVerdict]


def _run(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["v"] = asyncio.run(_await(value))
        except BaseException as exc:  # noqa: BLE001
            box["e"] = exc

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


async def _await(v: Any) -> Any:
    return await v


def _gateway_ready(gateway: Any) -> tuple[Any | None, str | None]:
    if gateway is None:
        return None, "no_gateway"
    try:
        gw = gateway.sync() if hasattr(gateway, "sync") else gateway
        health = _run(gw.health())
        state = getattr(getattr(health, "state", None), "value", getattr(health, "state", None))
        if str(state) != "READY":
            return None, f"gateway_{str(state).lower()}"
        return gw, None
    except Exception as exc:  # noqa: BLE001
        return None, f"gateway_error:{type(exc).__name__}"


def _classify_batch(gw: Any, pairs: list[tuple[Claim, Claim]]) -> dict[tuple[str, str], PairVerdict] | None:
    """One structured call over supplied pairs. Returns validated verdicts or None after bounded retries.
    Unknown/duplicate ids reject the whole response."""
    from app.connectors.llm.gateway import ModelRequest
    from app.connectors.llm.registry import CLAIM_RELATION_CLASSIFIER_V1 as spec

    allowed = {(p.id, c.id) for p, c in pairs}
    payload = json.dumps([
        {"proposed_id": p.id, "proposed_text": p.text[:600], "canonical_id": c.id, "canonical_text": c.text[:600]}
        for p, c in pairs], ensure_ascii=False)
    for attempt in range(MODEL_ATTEMPTS):
        req = ModelRequest(
            purpose=spec.purpose, system=spec.system,
            input="Classify each supplied claim pair. Return one verdict per pair, echoing proposed_id and canonical_id.",
            untrusted={"claim_pairs": payload}, prompt_version=spec.name, response_schema=ClassifierOutput,
            max_output_tokens=160 * len(pairs) + 128,
            metadata={"feature": "change_guard", "pairs": len(pairs), "attempt": attempt})
        try:
            resp = gw.generate_structured(req, ClassifierOutput)
            resp = _run(resp)
            out = resp.parsed if isinstance(resp.parsed, ClassifierOutput) else ClassifierOutput.model_validate(resp.parsed)
        except (ValidationError, ValueError, TypeError):
            continue  # invalid output: one bounded retry
        except Exception as exc:  # noqa: BLE001 - LLMUnavailable/LLMInvalidOutput/transport: no further retries
            log.info("changeguard.model_failed", error=type(exc).__name__)
            return None
        got: dict[tuple[str, str], PairVerdict] = {}
        bad = False
        for v in out.verdicts:
            key = (v.proposed_id, v.canonical_id)
            if key not in allowed or key in got:
                bad = True
                break
            got[key] = v
        if not bad:
            return got
    return None


# --------------------------------------------------------------------------- evaluation
def _finding(kind: str, p: Claim, c: Claim, rel: str, reasons: Sequence[str], conf: float, source: str) -> Finding:
    return Finding(kind, "high" if kind == "canonical_conflict" else "medium", p.id, c.id, rel,
                   list(reasons), round(float(conf), 3), source)


def _filter_canonical(canonical: Sequence[Claim | Mapping[str, Any]], now: date) -> tuple[list[Claim], dict[str, int]]:
    keep: list[Claim] = []
    ign = {"retired": 0, "not_yet_valid": 0, "expired": 0}
    for raw in canonical:
        c = raw if isinstance(raw, Claim) else canonical_from_mapping(raw)
        if c.status in ("retired", "inactive", "archived", "deleted"):
            ign["retired"] += 1
        elif c.valid_from and c.valid_from > now:
            ign["not_yet_valid"] += 1
        elif c.valid_until and c.valid_until < now:
            ign["expired"] += 1
        else:
            keep.append(c)
    return keep, {k: v for k, v in ign.items() if v}


def evaluate_canonical(proposed_claims: Sequence[Claim], canonical_claims: Sequence[Claim | Mapping[str, Any]], *,
                       gateway: Any = None, ranker: Any = None, now: date | datetime | None = None) -> EvaluationResult:
    today = now.date() if isinstance(now, datetime) else (now or datetime.now(UTC).date())
    canon, ignored = _filter_canonical(canonical_claims, today)
    res = EvaluationResult(ignored_canonical=ignored)
    if not canon:
        res.semantic_check = "skipped_no_canonical_truth"
        return res
    ambiguous: list[tuple[Claim, Claim, Relation]] = []
    for p in proposed_claims:
        for c in canon:
            res.pairs_compared += 1
            rel = compare_claims(p, c)
            if rel.needs_semantic:
                ambiguous.append((p, c, rel))
                continue
            if rel.relation != UNRELATED:
                res.pair_relations.append({"proposed": p.id, "canonical": c.id, **rel.as_dict()})
            if rel.relation == CONFLICTING:
                res.findings.append(_finding("canonical_conflict", p, c, CONFLICTING, rel.reasons, rel.confidence, "rules"))

    degraded: list[str] = []
    if len(ambiguous) > MAX_ESCALATED_PAIRS:
        for p, c, _ in ambiguous[MAX_ESCALATED_PAIRS:]:
            res.unresolved_pairs.append((p.id, c.id))
        ambiguous = ambiguous[:MAX_ESCALATED_PAIRS]
        degraded.append("escalation_cap")

    # ranker over ambiguous pairs only
    ranker_scores: dict[tuple[str, str], Any] = {}
    if ranker is None:
        degraded.append("no_ranker")
    elif ambiguous:
        try:
            scores = ranker.score_batch([(p.text, c.text, {}) for p, c, _ in ambiguous])
            ranker_scores = {(p.id, c.id): s for (p, c, _), s in zip(ambiguous, scores, strict=True)}
            res.ranker_used = True
            res.ranker_degraded = any(getattr(s, "degraded", False) for s in scores)
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"ranker_error:{type(exc).__name__}")
    elif ranker is not None:
        res.ranker_used = False
    if ranker_scores and res.ranker_degraded:
        degraded.append("ranker_degraded")

    gw, why = _gateway_ready(gateway)
    verdicts: dict[tuple[str, str], PairVerdict] = {}
    if gw is None:
        degraded.append(why or "no_gateway")
    elif ambiguous:
        for i in range(0, len(ambiguous), MAX_PAIRS_PER_CALL):
            chunk = ambiguous[i:i + MAX_PAIRS_PER_CALL]
            got = _classify_batch(gw, [(p, c) for p, c, _ in chunk])
            if got is None:
                degraded.append("model_failed")
                continue
            res.model_used = True
            verdicts.update(got)

    for p, c, rel in ambiguous:
        key = (p.id, c.id)
        rs, mv = ranker_scores.get(key), verdicts.get(key)
        f = _decide_ambiguous(p, c, rel, rs, mv)
        if f is not None:
            res.findings.append(f)
        if rs is None and mv is None:
            res.unresolved_pairs.append(key)
        res.pair_relations.append({"proposed": p.id, "canonical": c.id, "relation": f.relation if f else
                                   (mv.relation if mv else UNRELATED), "needs_semantic": True,
                                   "reasons": list(f.reasons if f else rel.reasons), "source": f.source if f else "none"})
    if res.unresolved_pairs:
        degraded.append("unresolved_ambiguous_pairs")
    res.degraded_reasons = list(dict.fromkeys(degraded))
    res.semantic_check = "degraded" if res.degraded_reasons else "ok"
    return res


def _decide_ambiguous(p: Claim, c: Claim, rel: Relation, rs: Any, mv: PairVerdict | None) -> Finding | None:
    """Precedence for ambiguous pairs (see module docstring)."""
    overlap = lexical_overlap(p, c) or bool(p.subject and c.subject)
    ranker_support_high = rs is not None and not rs.degraded and rs.support >= RANKER_SUPPORT_HIGH
    ranker_contra = rs is not None and rs.contradiction >= RANKER_CONTRA and rs.label == "contradiction"
    if mv is not None and mv.relation == CONFLICTING:
        if overlap and not ranker_support_high:
            return _finding("canonical_conflict", p, c, CONFLICTING, ["model_conflict", *rel.reasons],
                            mv.confidence, "model")
        why = "model_conflict_without_overlap" if not overlap else "model_conflict_vs_ranker_support"
        return _finding("canonical_uncertain", p, c, CONFLICTING, [why, *rel.reasons], mv.confidence * 0.7, "model")
    if ranker_contra:
        return _finding("canonical_uncertain", p, c, CONFLICTING, ["ranker_contradiction", *rel.reasons],
                        rs.contradiction, "ranker")
    return None
