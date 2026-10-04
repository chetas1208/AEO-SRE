"""Evidence gate: a root-cause hypothesis stays ``proposed`` until deterministic checks pass.

Claude/rules decide what to investigate; this module decides what is *established*. The gate is
pure and duck-typed (objects with attributes, dicts accepted for ``raw`` payloads); it never
mutates the hypothesis or evidence and never invents data.

Requirements (each individually switchable through ``EvidencePolicy``):
  profound_signal      usable, supporting Profound evidence
  metric_change        a supporting item carrying a non-zero quantitative delta
  content_evidence     usable, supporting owned/competitor/external content grounded in text
  citation_evidence    usable, supporting citation/source evidence (only where applicable)
  timestamp_alignment  content evidence observed before/around the incident (not long after)
  rationale            explicit written rationale on the hypothesis
  aggregate_confidence aggregate evidence confidence >= policy minimum
  hypothesis_confidence (optional) hypothesis' own confidence >= policy minimum
  cited_evidence       hypothesis cites evidence ids, and every cited id exists
  contradictions       no strong unresolved contradictory evidence

Evidence with status ``unavailable``/``failed`` and ``inference`` type evidence never count as
support. Contradictory evidence is always preserved and reported, even if the hypothesis did not
cite it.

Design note: requiring machine-checked evidence before a root cause is accepted is a pattern informed by
incident-response research (see docs/notes/research-inspiration.md); this implementation was written for
Profound Lift and is AEO-specific.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

# Requirement codes used in GateResult.missing / GateResult.checks
PROFOUND_SIGNAL = "profound_signal"
METRIC_CHANGE = "metric_change"
CONTENT_EVIDENCE = "content_evidence"
CITATION_EVIDENCE = "citation_evidence"
TIMESTAMP_ALIGNMENT = "timestamp_alignment"
RATIONALE = "rationale"
AGGREGATE_CONFIDENCE = "aggregate_confidence"
HYPOTHESIS_CONFIDENCE = "hypothesis_confidence"
CITED_EVIDENCE = "cited_evidence"
CONTRADICTIONS = "contradictions"
HYPOTHESIS_STATE = "hypothesis_state"

_NEVER_SUPPORT_STATUSES = frozenset({"unavailable", "failed"})
_DELTA_KEYS = frozenset({"delta", "change", "pct_change", "delta_pct", "percent_change", "z_score", "metric_delta"})
_PAIR_KEYS = (("before", "after"), ("baseline", "value"), ("previous", "current"), ("baseline", "current"))


@dataclass(frozen=True)
class EvidencePolicy:
    require_profound_signal: bool = True
    require_metric_change: bool = True
    require_content_evidence: bool = True
    # None = decide from the incident category (see citation_categories); True/False forces it.
    require_citation_evidence: bool | None = None
    citation_categories: frozenset[str] = frozenset({"competitor_citation_gain", "lost_citation_source"})
    require_timestamp_alignment: bool = True
    require_rationale: bool = True
    require_cited_evidence: bool = True
    reject_unknown_citations: bool = True

    min_rationale_chars: int = 20
    min_aggregate_confidence: float = 0.6
    min_hypothesis_confidence: float | None = None
    support_threshold: float = 0.5
    contradiction_threshold: float = 0.5
    # a contradicting item at/above this score blocks confirmation outright
    contradiction_block_threshold: float = 0.7
    # aggregate confidence is reduced by penalty * strongest contradiction score
    contradiction_penalty: float = 0.5

    alignment_tolerance: timedelta = timedelta(hours=24)
    alignment_lookback: timedelta | None = timedelta(days=30)

    profound_types: frozenset[str] = frozenset({"profound"})
    content_types: frozenset[str] = frozenset({"owned", "competitor", "external"})
    citation_kinds: frozenset[str] = frozenset({"citation", "citation_change", "citation_source", "source_attribution"})
    # unavailable/failed are always excluded regardless of what is configured here
    excluded_statuses: frozenset[str] = _NEVER_SUPPORT_STATUSES
    count_inference_as_support: bool = False


DEFAULT_POLICY = EvidencePolicy()


@dataclass
class GateResult:
    confirmed: bool
    reasons: list[str] = field(default_factory=list)  # failures when not confirmed, passes when confirmed
    missing: list[str] = field(default_factory=list)  # requirement codes that failed
    confidence: float = 0.0  # aggregate evidence confidence after contradiction penalty
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    supporting_ids: list[str] = field(default_factory=list)
    contradicting_ids: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    ignored: list[dict[str, Any]] = field(default_factory=list)  # evidence that could not count, with why

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed, "reasons": list(self.reasons), "missing": list(self.missing),
            "confidence": self.confidence, "checks": self.checks, "supporting_ids": list(self.supporting_ids),
            "contradicting_ids": list(self.contradicting_ids), "contradictions": list(self.contradictions),
            "ignored": list(self.ignored),
        }


# ------------------------------------------------------------------ duck-typing helpers


def _val(x: Any) -> str | None:
    if x is None:
        return None
    if isinstance(x, Enum):
        x = x.value
    return str(x).strip().lower()


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _dt(x: Any) -> datetime | None:
    if x is None:
        return None
    if isinstance(x, str):
        try:
            x = datetime.fromisoformat(x.strip())
        except ValueError:
            return None
    if not isinstance(x, datetime):
        return None
    return x if x.tzinfo else x.replace(tzinfo=UTC)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _eid(e: Any, idx: int) -> str:
    v = _get(e, "id")
    return str(v) if v is not None else f"evidence[{idx}]"


def _label(e: Any, eid: str) -> str:
    title = _get(e, "title")
    return f"{eid} ({title})" if title else eid


def _metric_change(e: Any) -> bool:
    """True if the evidence carries a measured, non-zero change (not just a mention of a metric)."""
    direct = _num(_get(e, "metric_delta"))
    if direct:
        return True

    def walk(node: Any, depth: int) -> bool:
        if depth > 3:
            return False
        if isinstance(node, Mapping):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() in _DELTA_KEYS and (n := _num(v)) is not None and n != 0:
                    return True
            low = {str(k).lower(): v for k, v in node.items()}
            for a, b in _PAIR_KEYS:
                na, nb = _num(low.get(a)), _num(low.get(b))
                if na is not None and nb is not None and na != nb:
                    return True
            return any(walk(v, depth + 1) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(v, depth + 1) for v in node)
        return False

    return walk(_get(e, "raw"), 0)


def _is_citation(e: Any, policy: EvidencePolicy) -> bool:
    if not _get(e, "url"):
        return False
    if _get(e, "is_citation") is True:
        return True
    raw = _get(e, "raw")
    if isinstance(raw, Mapping):
        for key in ("kind", "evidence_kind", "type"):
            if _val(raw.get(key)) in policy.citation_kinds:
                return True
        if raw.get("citation") or raw.get("citations"):
            return True
    return False


def _grounded(e: Any) -> bool:
    return bool(str(_get(e, "excerpt") or "").strip() or str(_get(e, "content_hash") or "").strip())


def _resolve_incident_at(hypothesis: Any, incident_at: Any) -> datetime | None:
    if (d := _dt(incident_at)) is not None:
        return d
    for obj in (hypothesis, _get(hypothesis, "incident")):
        if obj is None:
            continue
        for name in ("incident_at", "incident_detected_at", "first_observed_at", "detected_at"):
            if (d := _dt(_get(obj, name))) is not None:
                return d
    return None


def _resolve_category(hypothesis: Any, category: Any) -> str | None:
    if category is not None:
        return _val(category)
    for obj in (hypothesis, _get(hypothesis, "incident")):
        if obj is not None and (c := _val(_get(obj, "category"))):
            return c
    return None


# ------------------------------------------------------------------ the gate


def confirm_aeo_root_cause(
    hypothesis: Any,
    evidence: Iterable[Any],
    policy: EvidencePolicy | None = None,
    *,
    incident_at: datetime | str | None = None,
    category: Any = None,
) -> GateResult:
    """Decide whether ``hypothesis`` may move from proposed to confirmed.

    ``incident_at`` / ``category`` may be given explicitly or read from the hypothesis (or its
    ``incident`` attribute). When no incident time is known the earliest supporting Profound
    observation is the alignment anchor; with neither, timestamp alignment fails (never assumed).
    """
    p = policy or DEFAULT_POLICY
    items = list(evidence or [])
    excluded = set(p.excluded_statuses) | _NEVER_SUPPORT_STATUSES
    reasons: list[str] = []
    missing: list[str] = []
    checks: dict[str, dict[str, Any]] = {}
    ignored: list[dict[str, Any]] = []

    def record(code: str, ok: bool, detail: str, **extra: Any) -> None:
        checks[code] = {"ok": ok, "detail": detail, **extra}
        if not ok:
            missing.append(code)
            reasons.append(detail)

    hyp_status = _val(_get(hypothesis, "status"))
    if hyp_status == "rejected":
        record(HYPOTHESIS_STATE, False, "hypothesis was already rejected and cannot be confirmed")

    # --- index the evidence ------------------------------------------------------------
    by_id: dict[str, Any] = {}
    ids: list[str] = []
    for i, e in enumerate(items):
        eid = _eid(e, i)
        ids.append(eid)
        by_id.setdefault(eid, e)
    cited_ids = [str(x) for x in (_get(hypothesis, "evidence_ids") or [])]
    unknown = [c for c in cited_ids if c not in by_id]
    cited_set = set(cited_ids)

    if p.require_cited_evidence:
        if not cited_ids:
            record(CITED_EVIDENCE, False, "hypothesis cites no evidence ids; it must reference its evidence")
        elif unknown and p.reject_unknown_citations:
            record(CITED_EVIDENCE, False, f"hypothesis cites evidence that does not exist: {sorted(unknown)}")
        else:
            record(CITED_EVIDENCE, True, f"{len(cited_ids)} evidence id(s) cited", cited=cited_ids)
    elif unknown and p.reject_unknown_citations:
        record(CITED_EVIDENCE, False, f"hypothesis cites evidence that does not exist: {sorted(unknown)}")

    # --- classify ---------------------------------------------------------------------
    supports: list[tuple[str, Any, float]] = []  # (id, evidence, strength)
    contradicts: list[tuple[str, Any, float]] = []
    for i, e in enumerate(items):
        eid = ids[i]
        status, etype = _val(_get(e, "status")), _val(_get(e, "type"))
        s_score, c_score = _num(_get(e, "support_score")), _num(_get(e, "contradiction_score"))
        conf = _num(_get(e, "confidence"))
        strength = s_score if s_score is not None else conf

        usable = status not in excluded
        if (c_score or 0.0) >= p.contradiction_threshold and (c_score or 0.0) >= (strength or 0.0) and usable:
            contradicts.append((eid, e, c_score or 0.0))  # preserved even if not cited
            continue
        if not usable:
            ignored.append({"id": eid, "reason": f"status {status!r} never counts as support"})
            continue
        if cited_set and eid not in cited_set:
            continue  # not cited by the hypothesis: cannot support it
        if etype == "inference" and not p.count_inference_as_support:
            ignored.append({"id": eid, "reason": "inference evidence cannot support a root cause"})
            continue
        if strength is None or strength < p.support_threshold:
            ignored.append({"id": eid, "reason": "no support score/confidence at or above the support threshold"})
            continue
        supports.append((eid, e, strength))

    # contradictory evidence is always preserved in the result, cited or not
    contradictions = [
        {"id": eid, "title": _get(e, "title"), "type": _val(_get(e, "type")), "score": score,
         "excerpt": _get(e, "excerpt"), "url": _get(e, "url")}
        for eid, e, score in contradicts
    ]

    # --- rationale ---------------------------------------------------------------------
    if p.require_rationale:
        rationale = str(_get(hypothesis, "rationale") or "").strip()
        ok = len(rationale) >= p.min_rationale_chars
        record(RATIONALE, ok, "explicit supporting rationale present" if ok else
               f"hypothesis has no explicit supporting rationale (need >= {p.min_rationale_chars} chars)")

    # --- Profound signal ----------------------------------------------------------------
    profound = [(i, e, s) for i, e, s in supports if _val(_get(e, "type")) in p.profound_types]
    if p.require_profound_signal:
        record(PROFOUND_SIGNAL, bool(profound),
               f"{len(profound)} supporting Profound signal(s)" if profound else
               "no usable, supporting Profound signal evidence",
               ids=[i for i, _, _ in profound])

    # --- quantitative change ------------------------------------------------------------
    metric = [(i, e, s) for i, e, s in supports if _metric_change(e)]
    if p.require_metric_change:
        record(METRIC_CHANGE, bool(metric),
               f"{len(metric)} supporting evidence item(s) with a measured metric change" if metric else
               "no supporting evidence with a quantitative (non-zero) metric change",
               ids=[i for i, _, _ in metric])

    # --- content evidence -----------------------------------------------------------------
    content = [(i, e, s) for i, e, s in supports if _val(_get(e, "type")) in p.content_types and _grounded(e)]
    ungrounded = [i for i, e, _ in supports if _val(_get(e, "type")) in p.content_types and not _grounded(e)]
    for i in ungrounded:
        ignored.append({"id": i, "reason": "content evidence has no excerpt/content hash (not grounded in fetched content)"})
    if p.require_content_evidence:
        record(CONTENT_EVIDENCE, bool(content),
               f"{len(content)} supporting owned/competitor/external content item(s)" if content else
               "no usable, supporting owned/external content evidence grounded in fetched content",
               ids=[i for i, _, _ in content])

    # --- citation / source evidence ----------------------------------------------------------
    cat = _resolve_category(hypothesis, category)
    need_cit = p.require_citation_evidence
    if need_cit is None:
        need_cit = cat in p.citation_categories if cat else False
    citations = [(i, e, s) for i, e, s in supports if _is_citation(e, p)]
    if need_cit:
        record(CITATION_EVIDENCE, bool(citations),
               f"{len(citations)} supporting citation/source item(s)" if citations else
               f"category {cat!r} requires citation/source evidence and none supports the hypothesis",
               ids=[i for i, _, _ in citations])
    else:
        checks[CITATION_EVIDENCE] = {"ok": True, "detail": "not applicable for this incident", "applicable": False}

    # --- timestamp alignment -------------------------------------------------------------------
    if p.require_timestamp_alignment:
        anchor = _resolve_incident_at(hypothesis, incident_at)
        anchor_src = "incident"
        if anchor is None:
            profound_times = [t for _, e, _ in profound if (t := _dt(_get(e, "observed_at"))) is not None]
            if profound_times:
                anchor, anchor_src = min(profound_times), "earliest_profound_observation"
        aligned: list[str] = []
        misaligned: list[str] = []
        undated: list[str] = []
        if anchor is not None:
            lo = anchor - p.alignment_lookback if p.alignment_lookback is not None else None
            hi = anchor + p.alignment_tolerance
            for i, e, _ in content:
                t = _dt(_get(e, "observed_at"))
                if t is None:
                    undated.append(i)
                elif (lo is None or t >= lo) and t <= hi:
                    aligned.append(i)
                else:
                    misaligned.append(i)
        if anchor is None:
            record(TIMESTAMP_ALIGNMENT, False,
                   "no incident time or Profound observation time available; cannot align evidence timestamps")
        elif aligned:
            record(TIMESTAMP_ALIGNMENT, True, f"{len(aligned)} content item(s) observed before/around the incident",
                   anchor=anchor.isoformat(), anchor_source=anchor_src, ids=aligned, misaligned=misaligned)
        else:
            why = ("no content evidence to align" if not content else
                   f"content evidence not aligned with the incident ({len(misaligned)} observed outside the window, "
                   f"{len(undated)} without observed_at)")
            record(TIMESTAMP_ALIGNMENT, False, why, anchor=anchor.isoformat(), anchor_source=anchor_src,
                   misaligned=misaligned, undated=undated)

    # --- aggregate confidence --------------------------------------------------------------------
    if supports:
        mean = sum(s for _, _, s in supports) / len(supports)
    else:
        mean = 0.0
    max_contra = max((s for _, _, s in contradicts), default=0.0)
    aggregate = max(0.0, mean - p.contradiction_penalty * max_contra)
    record(AGGREGATE_CONFIDENCE, aggregate >= p.min_aggregate_confidence,
           f"aggregate evidence confidence {aggregate:.2f} meets minimum {p.min_aggregate_confidence:.2f}"
           if aggregate >= p.min_aggregate_confidence else
           f"aggregate evidence confidence {aggregate:.2f} is below minimum {p.min_aggregate_confidence:.2f}",
           value=aggregate, mean_support=mean, max_contradiction=max_contra)

    if p.min_hypothesis_confidence is not None:
        hc = _num(_get(hypothesis, "confidence"))
        ok = hc is not None and hc >= p.min_hypothesis_confidence
        record(HYPOTHESIS_CONFIDENCE, ok,
               f"hypothesis confidence {hc} meets minimum {p.min_hypothesis_confidence:.2f}" if ok else
               f"hypothesis confidence {hc} is below minimum {p.min_hypothesis_confidence:.2f}")

    # --- contradictions ---------------------------------------------------------------------------
    blocking = [c for c in contradictions if c["score"] >= p.contradiction_block_threshold]
    if blocking:
        record(CONTRADICTIONS, False,
               "unresolved contradictory evidence: " + ", ".join(_label(by_id[c["id"]], c["id"]) for c in blocking),
               ids=[c["id"] for c in blocking])
    elif contradictions:
        checks[CONTRADICTIONS] = {"ok": True, "detail": f"{len(contradictions)} weaker contradicting item(s) "
                                  "preserved and penalised in aggregate confidence",
                                  "ids": [c["id"] for c in contradictions]}
    else:
        checks[CONTRADICTIONS] = {"ok": True, "detail": "no contradicting evidence", "ids": []}

    confirmed = not missing
    if confirmed:
        reasons = [c["detail"] for c in checks.values()]
    return GateResult(
        confirmed=confirmed, reasons=reasons, missing=missing, confidence=aggregate, checks=checks,
        supporting_ids=[i for i, _, _ in supports], contradicting_ids=[c["id"] for c in contradictions],
        contradictions=contradictions, ignored=ignored,
    )
