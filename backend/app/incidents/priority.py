"""Intervention Priority: a transparent, multiplicative ranking heuristic (not a revenue estimate).

Priority = Prompt Demand x Buyer Intent x Incident Severity x Persona Importance
           x Competitive Displacement x Evidence Confidence x Remediation Feasibility

Every component is normalised to 0..1. The score is the weighted geometric mean of the components scaled to
0..100 (equal weights by default), which keeps the multiplicative semantics (a zero component zeroes the score,
no component can be compensated by another) while staying on a readable scale: all components at 0.7 -> 70.

A component the caller could not measure is passed as None. It is then filled with a configurable neutral
default and flagged `source="default"` in the breakdown, so the UI can show it as "unknown" instead of
presenting a guess as a measurement. Defaults, weights and thresholds are hand-set config, not learned.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import IncidentCategory, Severity

COMPONENTS: tuple[str, ...] = (
    "prompt_demand",
    "buyer_intent",
    "incident_severity",
    "persona_importance",
    "competitive_displacement",
    "evidence_confidence",
    "remediation_feasibility",
)

COMPONENT_LABELS: dict[str, str] = {
    "prompt_demand": "Prompt demand",
    "buyer_intent": "Buyer intent",
    "incident_severity": "Incident severity",
    "persona_importance": "Persona importance",
    "competitive_displacement": "Competitive displacement",
    "evidence_confidence": "Evidence confidence",
    "remediation_feasibility": "Remediation feasibility",
}


@dataclass(frozen=True)
class SeverityThresholds:
    """Priority score (0..100) at or above which a bucket applies. Below `medium` is `low`."""

    critical: float = 70.0
    high: float = 50.0
    medium: float = 30.0

    def __post_init__(self) -> None:
        if not (0 <= self.medium <= self.high <= self.critical <= 100):
            raise ValueError("thresholds must satisfy 0 <= medium <= high <= critical <= 100")


@dataclass(frozen=True)
class PriorityConfig:
    thresholds: SeverityThresholds = field(default_factory=SeverityThresholds)
    weights: Mapping[str, float] = field(default_factory=lambda: dict.fromkeys(COMPONENTS, 1.0))
    # Neutral values used when a component could not be measured. Flagged source="default" in the breakdown.
    defaults: Mapping[str, float] = field(
        default_factory=lambda: {
            "prompt_demand": 0.5,
            "buyer_intent": 0.5,
            "incident_severity": 0.5,
            "persona_importance": 0.5,
            "competitive_displacement": 0.3,
            "evidence_confidence": 0.3,
            "remediation_feasibility": 0.6,
        }
    )
    # Hand-set feasibility priors per category (how tractable remediation usually is). Config, not learned.
    feasibility_by_category: Mapping[str, float] = field(
        default_factory=lambda: {
            IncidentCategory.VISIBILITY_DROP.value: 0.70,
            IncidentCategory.COMPETITOR_CITATION_GAIN.value: 0.70,
            IncidentCategory.FACTUAL_CONFLICT.value: 0.85,
            IncidentCategory.LOST_CITATION_SOURCE.value: 0.50,
            IncidentCategory.PROMPT_VOLUME_SPIKE.value: 0.80,
            IncidentCategory.NEW_COMPETITOR_CONTENT.value: 0.65,
            IncidentCategory.STALE_INFORMATION.value: 0.90,
        }
    )
    demand_half_saturation: float = 500.0  # prompt volume at which demand = 0.5
    displacement_full_scale_pp: float = 30.0  # competitor share gain (pp) that maps to displacement = 1
    observe_below: float = 15.0  # below this score the incident is a candidate for ignore/observe


DEFAULT_CONFIG = PriorityConfig()


class ComponentScore(BaseModel):
    value: float = Field(ge=0.0, le=1.0)  # normalised 0..1
    display: float = Field(ge=0.0, le=100.0)  # 0..100 for the UI
    weight: float
    source: str  # "measured" | "heuristic" | "config" | "default" | caller-supplied label
    note: str = ""


class PriorityResult(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    breakdown: dict[str, ComponentScore]
    method: str = "weighted_geometric_mean"

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for `Incident.priority_breakdown`."""
        return {
            "score": round(self.score, 2),
            "method": self.method,
            "components": {
                k: {
                    "label": COMPONENT_LABELS.get(k, k),
                    "value": round(v.value, 4),
                    "display": round(v.display, 1),
                    "weight": v.weight,
                    "source": v.source,
                    "note": v.note,
                }
                for k, v in self.breakdown.items()
            },
        }

    @property
    def unknown_components(self) -> list[str]:
        return [k for k, v in self.breakdown.items() if v.source == "default"]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_priority(
    *,
    prompt_demand: float | None,
    buyer_intent: float | None,
    incident_severity: float | None,
    persona_importance: float | None,
    competitive_displacement: float | None,
    evidence_confidence: float | None,
    remediation_feasibility: float | None,
    config: PriorityConfig | None = None,
    sources: Mapping[str, str] | None = None,
    notes: Mapping[str, str] | None = None,
) -> PriorityResult:
    """Combine the seven normalised components into a 0..100 score with a per-component breakdown.

    `None` means "could not be measured": the config default is used and the component is flagged
    `source="default"`. Values outside 0..1 are clamped; NaN/inf raise ValueError.
    """
    cfg = config or DEFAULT_CONFIG
    raw = {
        "prompt_demand": prompt_demand,
        "buyer_intent": buyer_intent,
        "incident_severity": incident_severity,
        "persona_importance": persona_importance,
        "competitive_displacement": competitive_displacement,
        "evidence_confidence": evidence_confidence,
        "remediation_feasibility": remediation_feasibility,
    }
    sources = sources or {}
    notes = notes or {}
    weights = {k: float(cfg.weights.get(k, 1.0)) for k in COMPONENTS}
    if any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("weights must be non-negative with a positive sum")
    total_w = sum(weights.values())

    breakdown: dict[str, ComponentScore] = {}
    log_sum = 0.0
    zero = False
    for name in COMPONENTS:
        val = raw[name]
        if val is None:
            value, source = _clamp01(float(cfg.defaults.get(name, 0.5))), "default"
            note = notes.get(name, "not measured; neutral default applied")
        else:
            if not math.isfinite(val):
                raise ValueError(f"component {name} is not finite: {val!r}")
            value, source = _clamp01(float(val)), sources.get(name, "measured")
            note = notes.get(name, "")
        breakdown[name] = ComponentScore(
            value=value, display=round(value * 100, 1), weight=weights[name] / total_w, source=source, note=note
        )
        if weights[name] > 0:
            if value <= 0.0:
                zero = True
            else:
                log_sum += (weights[name] / total_w) * math.log(value)
    score = 0.0 if zero else 100.0 * math.exp(log_sum)
    return PriorityResult(score=round(min(100.0, max(0.0, score)), 4), breakdown=breakdown)


def severity_from_priority(score: float, thresholds: SeverityThresholds | None = None) -> Severity:
    t = thresholds or DEFAULT_CONFIG.thresholds
    if score >= t.critical:
        return Severity.CRITICAL
    if score >= t.high:
        return Severity.HIGH
    if score >= t.medium:
        return Severity.MEDIUM
    return Severity.LOW


def is_below_action_threshold(score: float, config: PriorityConfig | None = None) -> bool:
    """True when the score is low enough that ignore/observe is the sensible default."""
    return score < (config or DEFAULT_CONFIG).observe_below


# --- component normalisers -------------------------------------------------------------------------------


def demand_from_volume(volume: float | None, config: PriorityConfig | None = None) -> float | None:
    """Saturating map v/(v+h): volume == half_saturation -> 0.5, never reaches 1."""
    if volume is None or not math.isfinite(volume):
        return None
    cfg = config or DEFAULT_CONFIG
    v = max(0.0, volume)
    return v / (v + cfg.demand_half_saturation)


def displacement_from_competitor_gain(gain_pp: float | None, config: PriorityConfig | None = None) -> float | None:
    if gain_pp is None or not math.isfinite(gain_pp):
        return None
    cfg = config or DEFAULT_CONFIG
    return _clamp01(max(0.0, gain_pp) / cfg.displacement_full_scale_pp)


def feasibility_for_category(category: str | IncidentCategory, config: PriorityConfig | None = None) -> float | None:
    cfg = config or DEFAULT_CONFIG
    key = category.value if isinstance(category, IncidentCategory) else str(category)
    return cfg.feasibility_by_category.get(key)


_HIGH_INTENT = re.compile(
    r"\b(pricing|price|prices|cost|costs|buy|purchase|demo|trial|quote|vs\.?|versus|alternatives?|compare|"
    r"comparison|best|top|review|reviews|enterprise|sso|saml|migrate|migration|switch|plans?)\b",
    re.IGNORECASE,
)
_MID_INTENT = re.compile(
    r"\b(how to|setup|set up|integrat\w*|features?|support|compatible|works? with|pros and cons|security|"
    r"compliance|soc ?2|gdpr)\b",
    re.IGNORECASE,
)


def buyer_intent_from_prompts(prompts: Sequence[str] | None) -> float | None:
    """Transparent keyword heuristic: commercial/evaluation wording scores high, how-to medium, else low.
    Returns None without prompts. This is a heuristic prior, flagged as such by callers."""
    cleaned = [p for p in (prompts or []) if isinstance(p, str) and p.strip()]
    if not cleaned:
        return None
    total = 0.0
    for p in cleaned:
        if _HIGH_INTENT.search(p):
            total += 0.9
        elif _MID_INTENT.search(p):
            total += 0.5
        else:
            total += 0.2
    return total / len(cleaned)


def persona_importance_from_config(
    personas: Sequence[Any] | None, persona: str | None = None
) -> float | None:
    """Look up an importance weight in `Organization.personas`.

    Accepts a list of dicts ({"name", "importance"|"weight"|"priority"}) or plain strings (no weight -> None).
    Weights above 1 are interpreted as 0..100. Without a named persona (or an unknown one) returns None:
    persona importance is never guessed."""
    weights: dict[str, float] = {}
    for p in personas or []:
        if not isinstance(p, Mapping):
            continue
        name = str(p.get("name") or p.get("persona") or "").strip().lower()
        w = next((p[k] for k in ("importance", "weight", "priority") if isinstance(p.get(k), (int, float))), None)
        if name and w is not None:
            weights[name] = _clamp01(float(w) / 100.0 if w > 1 else float(w))
    if not weights:
        return None
    if persona:
        return weights.get(persona.strip().lower())
    return None
