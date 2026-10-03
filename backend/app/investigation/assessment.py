"""Second-stage machine assessment of RCA hypotheses (pure; no I/O, no model).

The evidence gate (`evidence_gate.confirm_aeo_root_cause`) answers "is there enough grounded, aligned, uncontradicted
evidence?". This stage answers the questions the gate cannot:
  * which taxonomy cause is this, and how do the competing hypotheses rank on evidence-derived confidence?
  * was counterevidence actively searched for it, and was anything found?
  * did the control group move (category/platform/model movement) before we blame the brand?
  * is the claim scoped (a ChatGPT-only or persona-only regression is never generalized)?
A hypothesis may be CONFIRMED only if the gate AND every check here pass. The model cannot confirm anything: its
confidence is one weak feature, and nothing in this module reads a model verdict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.investigation.confidence import CONFIRM_MIN, ConfidenceResult, compute_confidence
from app.investigation.control import ControlAssessment, ControlVerdict
from app.investigation.counterevidence import CounterevidenceReport, run_checks
from app.investigation.taxonomy import BRAND_SPECIFIC, RootCause, cause_of_meta

AMBIGUITY_MARGIN = 0.03
UNRESOLVED_COUNTER = 0.7
_ADVERSE_UP = {"competitor_share", "lost_sources", "factual_conflicts", "new_competitor_content", "stale_sources",
               "prompt_volume", "avg_position"}


@dataclass
class Assessment:
    hypothesis_id: str
    title: str
    cause: RootCause
    confidence: ConfidenceResult
    counter: CounterevidenceReport
    gate_confirmed: bool
    blockers: list[str] = field(default_factory=list)
    rank: int = 0

    @property
    def allow_confirm(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {"hypothesis_id": self.hypothesis_id, "title": self.title, "cause": self.cause.value, "rank": self.rank,
                "confidence": self.confidence.to_dict(), "counterevidence": self.counter.to_dict(),
                "gate_confirmed": self.gate_confirmed, "blockers": list(self.blockers),
                "allow_confirm": self.allow_confirm}


@dataclass
class InvestigationAssessment:
    assessments: list[Assessment]
    control: ControlAssessment | None
    scope: dict[str, Any]
    alternatives: list[dict[str, Any]]
    outcome: str  # confirmed | insufficient_evidence

    @property
    def confirmed_ids(self) -> list[str]:
        return [a.hypothesis_id for a in self.assessments if a.allow_confirm]

    def get(self, hid: str) -> Assessment | None:
        return next((a for a in self.assessments if a.hypothesis_id == hid), None)

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "scope": self.scope, "control": self.control.to_dict() if self.control else None,
                "ranking": [a.to_dict() for a in self.assessments], "alternatives": self.alternatives,
                "confirmed": self.confirmed_ids}


def _get(o: Any, name: str, default: Any = None) -> Any:
    return o.get(name, default) if isinstance(o, Mapping) else getattr(o, name, default)


def _raw(o: Any) -> dict[str, Any]:
    r = _get(o, "raw")
    return r if isinstance(r, dict) else {}


def direction_agreement(evidence: Sequence[Any]) -> float:
    """Share of metric-change evidence whose delta is adverse (the metric moved the bad way)."""
    vals = []
    for e in evidence:
        raw = _raw(e)
        if raw.get("kind") != "metric_change" or not isinstance(raw.get("delta"), (int, float)) or raw["delta"] == 0:
            continue
        adverse = raw["delta"] > 0 if raw.get("metric") in _ADVERSE_UP else raw["delta"] < 0
        vals.append(1.0 if adverse else 0.0)
    return sum(vals) / len(vals) if vals else 0.5  # no measured delta: neutral, never full credit


def scope_of_incident(incident: Any) -> dict[str, Any]:
    sig = (_get(incident, "context") or {}).get("signature") or {}
    platform, persona = sig.get("platform"), sig.get("persona")
    return {"platform": platform, "persona": persona, "generalizes_beyond_scope": False,
            "statement": ("Applies to " + ", ".join(x for x in (f"platform {platform}" if platform else "",
                                                                f"persona {persona}" if persona else "") if x)
                          + " only; not established for other platforms/personas.") if (platform or persona)
            else "Applies to the whole prompt cluster as observed (all platforms and personas in the series)."}


def assess_investigation(
    incident: Any,
    hypotheses: Sequence[Any],  # id, title, evidence_ids, confidence, produced_by
    hypothesis_meta: Mapping[str, Any],
    evidence: Sequence[Any],
    gates: Mapping[str, Any],  # hypothesis id -> GateResult-like (confirmed, supporting_ids, checks)
    control: ControlAssessment | None,
    *,
    counter_overrides: Mapping[str, CounterevidenceReport] | None = None,
) -> InvestigationAssessment:
    ev_by_id = {str(_get(e, "id")): e for e in evidence}
    ctx = _get(incident, "context") or {}
    n_signals = len(ctx.get("correlated_signals") or []) or 1
    agree = direction_agreement(evidence)
    out: list[Assessment] = []
    for h in hypotheses:
        hid = str(_get(h, "id"))
        meta = hypothesis_meta.get(hid) or {}
        if not meta.get("actionable", True):
            continue
        cause = cause_of_meta(meta, str(_get(h, "title") or ""))
        gate = gates.get(hid)
        sup_ids = [str(i) for i in (_get(gate, "supporting_ids", []) or [])] if gate else []
        supporting = [ev_by_id[i] for i in sup_ids if i in ev_by_id]
        scores = [float(s) for e in supporting if (s := _get(e, "support_score", _get(e, "confidence"))) is not None]
        mean_support = sum(scores) / len(scores) if scores else 0.0
        counter = (counter_overrides or {}).get(hid) or run_checks(cause, evidence, control, set(sup_ids))
        conf = compute_confidence(
            cause, supporting, gate_checks=_get(gate, "checks", {}) if gate else {}, mean_support=mean_support,
            prior=_get(h, "confidence"), counter=counter, control=control, correlated_signals=n_signals,
            direction_agreement=agree,
        )
        a = Assessment(hid, str(_get(h, "title") or ""), cause, conf, counter, bool(gate and _get(gate, "confirmed")))
        out.append(a)
    out.sort(key=lambda a: (-a.confidence.value, a.cause.value))
    for i, a in enumerate(out, 1):
        a.rank = i
        if not a.gate_confirmed:
            a.blockers.append("evidence_gate_not_passed")
        if not a.counter.performed:
            a.blockers.append("counterevidence_not_searched")
        elif a.counter.found and max(c.strength for c in a.counter.found) >= UNRESOLVED_COUNTER:
            a.blockers.append("unresolved_counterevidence:" + ",".join(c.code for c in a.counter.found))
        if control is not None and control.verdict is ControlVerdict.CATEGORY_WIDE and a.cause in BRAND_SPECIFIC:
            a.blockers.append("category_wide_movement_rules_out_brand_specific_cause")
        if a.confidence.value < CONFIRM_MIN:
            a.blockers.append(f"computed_confidence_{a.confidence.value:.2f}_below_{CONFIRM_MIN:.2f}")
    # near-tie between different causes that both clear the checks: do not crown one
    clear = [a for a in out if not a.blockers]
    if len(clear) >= 2 and clear[0].cause != clear[1].cause and \
            clear[0].confidence.value - clear[1].confidence.value < AMBIGUITY_MARGIN:
        for a in clear[:2]:
            a.blockers.append(f"ambiguous_between_{clear[0].cause.value}_and_{clear[1].cause.value}")
    alternatives: list[dict[str, Any]] = [
        {"cause": a.cause.value, "hypothesis_id": a.hypothesis_id, "confidence": a.confidence.value,
         "status": "leading" if a.rank == 1 else "competing", "blockers": a.blockers} for a in out
    ]
    if control is not None and control.verdict is ControlVerdict.CATEGORY_WIDE and not any(
            a.cause is RootCause.MODEL_VARIANCE for a in out):
        alternatives.append({"cause": RootCause.MODEL_VARIANCE.value, "hypothesis_id": None, "confidence": None,
                             "status": "favored_by_control_movement", "note": control.note})
    outcome = "confirmed" if any(a.allow_confirm for a in out) else "insufficient_evidence"
    if outcome == "insufficient_evidence":
        alternatives.append({"cause": RootCause.INSUFFICIENT_EVIDENCE.value, "hypothesis_id": None, "confidence": None,
                             "status": "standing"})
    return InvestigationAssessment(out, control, scope_of_incident(incident), alternatives, outcome)
