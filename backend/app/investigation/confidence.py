"""Confidence computed from evidence features. The model's own confidence is at most ONE weak feature (weight 0.05).

confidence = sum(weight_i * feature_i) - 0.5 * counterevidence_penalty, then capped:
  * family count          distinct evidence families supporting the hypothesis (profound metric, profound citation/answer,
                          owned page, competitor page, third-party page, fanout)
  * temporal alignment    fraction of supporting content observed before/around the incident (from the gate)
  * signal consistency    metric-change evidence agreeing in direction x number of corroborating signal families
  * support               mean EvidenceRanker support of supporting items (from the gate)
  * source directness     fetched pages we can read directly (owned / competitor) vs secondary sources. An ordinal
                          heuristic, NOT an authority score
  * prior                 the hypothesis' own (rule or LLM) confidence, weak
Caps: a category-wide control movement halves brand-specific causes; an un-run control or counterevidence search caps
confidence below "high"; "high" (>= 0.80) additionally needs >= 2 families and a brand-specific control verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.investigation.control import ControlAssessment, ControlVerdict
from app.investigation.counterevidence import CounterevidenceReport
from app.investigation.taxonomy import BRAND_SPECIFIC, RootCause

WEIGHTS = {"families": 0.20, "temporal": 0.15, "consistency": 0.15, "support": 0.30, "directness": 0.15, "prior": 0.05}
CONFIRM_MIN = 0.60
HIGH_MIN = 0.80
MEDIUM_CAP = 0.79
UNCHECKED_CAP = 0.70
DIRECTNESS = {"OWNED": 1.0, "COMPETITOR": 1.0, "THIRD_PARTY": 0.6, "COMMUNITY": 0.3, "UNKNOWN": 0.1}


@dataclass
class ConfidenceResult:
    value: float
    features: dict[str, float]
    weights: dict[str, float]
    penalty: float
    caps: list[str] = field(default_factory=list)
    tier: str = "low"  # low | medium | high
    families: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "tier": self.tier, "features": self.features, "weights": self.weights,
                "counter_penalty": self.penalty, "caps": self.caps, "families": self.families}


def _get(o: Any, name: str, default: Any = None) -> Any:
    return o.get(name, default) if isinstance(o, dict) else getattr(o, name, default)


def _raw(o: Any) -> dict[str, Any]:
    r = _get(o, "raw")
    return r if isinstance(r, dict) else {}


def evidence_family(e: Any) -> str:
    t = str(getattr(_get(e, "type"), "value", _get(e, "type")) or "").lower()
    raw = _raw(e)
    if t == "profound":
        kind = raw.get("kind")
        if kind == "query_fanout_shift":
            return "profound_fanout"
        if raw.get("citation_change") or raw.get("citations") or raw.get("citation_details"):
            return "profound_citation"
        return "profound_metric"
    if t in ("owned", "competitor"):
        return f"{t}_page"
    return "third_party_page"


def _source_category(e: Any) -> str:
    cat = _raw(e).get("source_category")
    if cat in DIRECTNESS:
        return cat
    t = str(_get(e, "type") or "").lower()
    return {"owned": "OWNED", "competitor": "COMPETITOR", "external": "THIRD_PARTY"}.get(t, "UNKNOWN")


def compute_confidence(
    cause: RootCause,
    supporting: Sequence[Any],
    *,
    gate_checks: dict[str, Any] | None,
    mean_support: float,
    prior: float | None,
    counter: CounterevidenceReport | None,
    control: ControlAssessment | None,
    correlated_signals: int = 1,
    direction_agreement: float = 1.0,
) -> ConfidenceResult:
    fams = sorted({evidence_family(e) for e in supporting})
    f_fam = min(1.0, max(0, len(fams) - 1) / 2.0)
    tcheck = (gate_checks or {}).get("timestamp_alignment") or {}
    content_n = max(1, len(tcheck.get("ids") or []) + len(tcheck.get("misaligned") or []) + len(tcheck.get("undated") or []))
    f_temporal = (len(tcheck.get("ids") or []) / content_n) if tcheck.get("ok") else 0.0
    f_consistency = max(0.0, min(1.0, direction_agreement * (0.5 + 0.5 * min(1.0, max(0, correlated_signals - 1) / 2.0))))
    f_support = max(0.0, min(1.0, mean_support))
    web = [e for e in supporting if evidence_family(e).endswith("_page")]
    f_direct = (sum(DIRECTNESS[_source_category(e)] for e in web) / len(web)) if web else 0.0
    f_prior = max(0.0, min(1.0, prior)) if prior is not None else 0.0
    feats = {"families": round(f_fam, 3), "temporal": round(f_temporal, 3), "consistency": round(f_consistency, 3),
             "support": round(f_support, 3), "directness": round(f_direct, 3), "prior": round(f_prior, 3)}
    raw = sum(WEIGHTS[k] * v for k, v in feats.items())
    penalty = counter.penalty if counter is not None else 0.0
    value = raw - 0.5 * penalty
    caps: list[str] = []
    if control is not None and control.verdict is ControlVerdict.CATEGORY_WIDE and cause in BRAND_SPECIFIC:
        value *= 0.5
        caps.append("category_wide_control_movement")
    if counter is None or not counter.performed:
        value = min(value, UNCHECKED_CAP)
        caps.append("counterevidence_not_searched")
    if control is None or not control.checked:
        value = min(value, UNCHECKED_CAP)
        caps.append("control_movement_not_established")
    high_ok = len(fams) >= 2 and control is not None and control.verdict is ControlVerdict.BRAND_SPECIFIC \
        and counter is not None and counter.performed and not counter.found
    if value >= HIGH_MIN and not high_ok:
        value = min(value, MEDIUM_CAP)
        caps.append("high_confidence_requirements_unmet")
    value = round(max(0.0, min(1.0, value)), 3)
    tier = "high" if value >= HIGH_MIN else "medium" if value >= CONFIRM_MIN else "low"
    return ConfidenceResult(value, feats, dict(WEIGHTS), round(penalty, 3), caps, tier, fams)
