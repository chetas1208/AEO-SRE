"""Context encoding for the intervention bandit.

`encode_context(...)` returns a fixed-order float vector (see `FEATURE_NAMES`). Every feature is
normalized to a bounded range so that LinUCB/Thompson ridge regression is well conditioned:

| feature            | raw input                                   | normalization                      | range   |
|--------------------|---------------------------------------------|------------------------------------|---------|
| visibility_delta   | relative change vs baseline (fraction)      | clip(x, -1, 1); -0.25 = 25% drop   | [-1, 1] |
| citation_delta     | relative change in citation share           | clip(x, -1, 1)                     | [-1, 1] |
| accuracy_delta     | relative change in answer accuracy          | clip(x, -1, 1)                     | [-1, 1] |
| competitor_delta   | relative change in competitor presence;     | clip(x, -1, 1); + = competitor up  | [-1, 1] |
| prompt_volume      | raw prompt count/volume                     | log1p(v) / log1p(PROMPT_VOLUME_REF)| [0, 1]  |
| buyer_intent       | share of commercial-intent prompts          | clip(x, 0, 1)                      | [0, 1]  |
| source_authority   | authority of the cited source               | clip(x, 0, 1)                      | [0, 1]  |
| source_freshness   | direct score, or age in days                | exp(-age_days / FRESHNESS_TAU_DAYS)| [0, 1]  |
| owned_source       | cited source is on an owned domain          | bool -> {0, 1}                     | {0, 1}  |
| third_party_source | cited source is third-party                 | bool -> {0, 1}                     | {0, 1}  |
| content_exists     | we already have a page covering the topic   | bool -> {0, 1}                     | {0, 1}  |
| factual_conflict   | conflict confidence (gate-confirmed or not) | clip(x, 0, 1)                      | [0, 1]  |
| persona_value      | value of the affected persona               | clip(x, 0, 1)                      | [0, 1]  |
| action_cost        | expected remediation effort for the incident| clip(x, 0, 1)                      | [0, 1]  |
| historical_success | smoothed success rate of similar past fixes | clip(x, 0, 1); unknown -> 0.5      | [0, 1]  |
| incident_type=*    | IncidentCategory                            | one-hot                            | {0, 1}  |
| bias               | constant intercept                          | 1.0                                | 1       |

Missing inputs are NOT invented: they encode to a documented neutral value (0 for deltas, volume, cost and
owned/third-party/conflict flags; 0.5 for quality scores and content_exists = unknown) and are listed in `ContextVector.missing`.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.domain.enums import IncidentCategory

SCHEMA = "ctx-v2"  # current feature schema; persisted on every PolicyVersion, PolicyDecision and context vector
LEGACY_SCHEMA = "ctx-v1"
PROMPT_VOLUME_REF = 10_000.0
FRESHNESS_TAU_DAYS = 180.0
PLATFORM_REF = 10.0
MEMORY_N_REF = 20.0

SCALAR_FEATURES: tuple[str, ...] = (
    "visibility_delta",
    "citation_delta",
    "accuracy_delta",
    "competitor_delta",
    "prompt_volume",
    "buyer_intent",
    "source_authority",
    "source_freshness",
    "owned_source",
    "third_party_source",
    "content_exists",
    "factual_conflict",
    "persona_value",
    "action_cost",
    "historical_success",
)
INCIDENT_TYPES: tuple[IncidentCategory, ...] = tuple(IncidentCategory)
INCIDENT_FEATURES: tuple[str, ...] = tuple(f"incident_type={c.value}" for c in INCIDENT_TYPES)
BIAS = "bias"
FEATURE_NAMES_V1: tuple[str, ...] = (*SCALAR_FEATURES, *INCIDENT_FEATURES, BIAS)
# ctx-v2 appends (after the bias, so every ctx-v1 index is unchanged and v1 states upgrade by zero-padding):
# persisted-state features that v1 could not see, plus the historical-memory aggregates (numbers only, no raw text).
V2_SCALAR_FEATURES: tuple[str, ...] = (
    "severity",
    "priority",
    "evidence_confidence",
    "root_cause_confirmed",
    "platform_breadth",
    "memory_similar_n",
    "memory_mean_reward",
    "memory_favorable_rate",
    "memory_uncertainty",
)
ROOT_CAUSE_LAYERS: tuple[str, ...] = ("ai_engine", "citation", "owned_content", "competitor", "external_web",
                                      "canonical_truth", "undetermined")
V2_ROOT_CAUSE_FEATURES: tuple[str, ...] = tuple(f"root_cause={x}" for x in ROOT_CAUSE_LAYERS)
FEATURE_NAMES: tuple[str, ...] = (*FEATURE_NAMES_V1, *V2_SCALAR_FEATURES, *V2_ROOT_CAUSE_FEATURES)
FEATURE_SCHEMAS: dict[str, tuple[str, ...]] = {LEGACY_SCHEMA: FEATURE_NAMES_V1, SCHEMA: FEATURE_NAMES}
SCHEMA_DIMS: dict[str, int] = {k: len(v) for k, v in FEATURE_SCHEMAS.items()}
# Names shown in the UI (the intercept is an implementation detail).
UI_FEATURE_NAMES: tuple[str, ...] = tuple(n for n in FEATURE_NAMES if n != BIAS)
DIM = len(FEATURE_NAMES)
_INDEX = {n: i for i, n in enumerate(FEATURE_NAMES)}

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "visibility_delta": "Relative change in AI answer visibility vs baseline (negative = drop)",
    "citation_delta": "Relative change in citation share vs baseline",
    "accuracy_delta": "Relative change in answer accuracy vs baseline",
    "competitor_delta": "Relative change in competitor presence (positive = competitor gaining)",
    "prompt_volume": "Prompt volume affected (log-scaled to 0-1)",
    "buyer_intent": "Share of commercial/buyer-intent prompts",
    "source_authority": "Authority of the cited source",
    "source_freshness": "Freshness of the cited source (1 = fresh)",
    "owned_source": "Cited source is on an owned domain",
    "third_party_source": "Cited source is a third-party domain",
    "content_exists": "We already publish content covering this topic",
    "factual_conflict": "Confidence that a factual conflict exists",
    "persona_value": "Business value of the affected persona",
    "action_cost": "Expected remediation effort for this incident",
    "historical_success": "Smoothed success rate of similar past interventions (0.5 = unknown)",
    **{f"incident_type={c.value}": f"Incident category is {c.value}" for c in INCIDENT_TYPES},
    "severity": "Incident severity bucket (low 0.25 .. critical 1.0)",
    "priority": "Intervention priority score / 100",
    "evidence_confidence": "Confidence of the best-supported root-cause hypothesis (0.5 = unknown)",
    "root_cause_confirmed": "Evidence gate confirmed a root cause (1) or not (0)",
    "platform_breadth": "Number of distinct AI platforms in the incident signals (log-scaled)",
    "memory_similar_n": "Similar past rewarded experiments found (log-scaled count)",
    "memory_mean_reward": "Mean reward of similar past experiments (0 when none)",
    "memory_favorable_rate": "Share of similar past experiments with a favorable outcome (0.5 = unknown)",
    "memory_uncertainty": "Uncertainty of the memory aggregate (1 = no history)",
    **{f"root_cause={x}": f"Confirmed or leading root-cause layer is {x}" for x in ROOT_CAUSE_LAYERS},
}

# Unknown -> neutral: 0.5 for [0,1] quality scores and the tri-state flag content_exists (0.5 = unknown, so rules
# that require "exists" (>0.5) or "missing" (<0.5) both stay silent); 0 for deltas, volume, cost and the
# owned/third-party/conflict flags.
_NEUTRAL = {"historical_success": 0.5, "content_exists": 0.5, "source_freshness": 0.5, "source_authority": 0.5,
            "buyer_intent": 0.5, "persona_value": 0.5, "severity": 0.5, "evidence_confidence": 0.5,
            "memory_favorable_rate": 0.5, "memory_uncertainty": 1.0}
_SIGNED = {"visibility_delta", "citation_delta", "accuracy_delta", "competitor_delta", "memory_mean_reward"}


def relative_delta(current: float, baseline: float) -> float:
    """(current - baseline) / baseline, clipped to [-1, 1]. Baseline 0 -> sign of the move (+/-1) or 0."""
    if baseline == 0:
        return 0.0 if current == 0 else math.copysign(1.0, current)
    return float(np.clip((current - baseline) / abs(baseline), -1.0, 1.0))


def smoothed_success_rate(successes: int, trials: int) -> float:
    """Laplace-smoothed success rate; (0, 0) -> 0.5."""
    return (successes + 1.0) / (trials + 2.0)


@dataclass
class ContextVector:
    values: np.ndarray
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def names(self) -> tuple[str, ...]:
        return FEATURE_NAMES

    @property
    def incident_type(self) -> IncidentCategory | None:
        return incident_type_of(self.values)

    def to_dict(self) -> dict[str, float]:
        return {n: float(v) for n, v in zip(FEATURE_NAMES, self.values, strict=True)}

    def to_json(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "names": list(FEATURE_NAMES), "values": [float(v) for v in self.values],
                "missing": list(self.missing)}


def _clip(name: str, v: float) -> float:
    lo = -1.0 if name in _SIGNED else 0.0
    return float(np.clip(v, lo, 1.0))


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _category(v: Any) -> IncidentCategory | None:
    if v is None:
        return None
    if isinstance(v, IncidentCategory):
        return v
    try:
        return IncidentCategory(str(v))
    except ValueError:
        return None


_METRIC_KEYS = {
    "visibility": "visibility_delta",
    "citation": "citation_delta",
    "accuracy": "accuracy_delta",
    "competitor": "competitor_delta",
}


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _from_incident(incident: Any) -> tuple[dict[str, Any], IncidentCategory | None]:
    """Pull raw feature inputs from an Incident ORM row or dict. Only reads values that exist.

    Precedence: `incident.context` (explicit feature inputs by name) > derived from `incident.metrics`
    (list of dicts with metric name + delta/value/baseline).
    """
    raw: dict[str, Any] = {}
    for item in _get(incident, "metrics") or []:
        name = str(_get(item, "metric") or _get(item, "name") or _get(item, "key") or _get(item, "kind") or "").lower()
        target = next((t for k, t in _METRIC_KEYS.items() if k in name), None)
        if target is None or target in raw:
            continue
        d = _as_float(_get(item, "delta_pct"))
        if d is not None:
            raw[target] = d / 100.0
            continue
        d = _as_float(_get(item, "delta"))
        if d is not None and _as_float(_get(item, "baseline")) in (None, 0.0):
            raw[target] = d
            continue
        cur, base = _as_float(_get(item, "value")), _as_float(_get(item, "baseline"))
        if cur is not None and base is not None:
            raw[target] = relative_delta(cur, base)
        elif d is not None and base:
            raw[target] = d / abs(base)
    ctx = _get(incident, "context")
    if isinstance(ctx, Mapping):
        for k in (*SCALAR_FEATURES, *V2_SCALAR_FEATURES, "source_age_days", "prompt_demand", "root_cause_layer"):
            if ctx.get(k) is not None:
                raw[k] = ctx[k]
    return raw, _category(_get(incident, "category"))


def encode_context(
    incident: Any = None,
    *,
    incident_type: IncidentCategory | str | None = None,
    visibility_delta: float | None = None,
    citation_delta: float | None = None,
    accuracy_delta: float | None = None,
    competitor_delta: float | None = None,
    prompt_volume: float | None = None,
    buyer_intent: float | None = None,
    source_authority: float | None = None,
    source_freshness: float | None = None,
    source_age_days: float | None = None,
    owned_source: bool | float | None = None,
    third_party_source: bool | float | None = None,
    content_exists: bool | float | None = None,
    factual_conflict: bool | float | None = None,
    persona_value: float | None = None,
    action_cost: float | None = None,
    historical_success: float | None = None,
    prompt_demand: float | None = None,
    severity: float | None = None,
    priority: float | None = None,
    evidence_confidence: float | None = None,
    root_cause_confirmed: bool | float | None = None,
    platform_breadth: float | None = None,
    memory_similar_n: float | None = None,
    memory_mean_reward: float | None = None,
    memory_favorable_rate: float | None = None,
    memory_uncertainty: float | None = None,
    root_cause_layer: str | None = None,
    return_details: bool = False,
) -> np.ndarray | ContextVector:
    """Encode an incident (+ explicit overrides) into the fixed-order feature vector.

    Explicit keyword arguments win over values derived from `incident`. Returns an ndarray of length
    `DIM` (names in `FEATURE_NAMES`), or a `ContextVector` (with `missing` listed) if `return_details`.
    """
    raw, cat = _from_incident(incident) if incident is not None else ({}, None)
    explicit = {
        "visibility_delta": visibility_delta, "citation_delta": citation_delta,
        "accuracy_delta": accuracy_delta, "competitor_delta": competitor_delta,
        "prompt_volume": prompt_volume, "buyer_intent": buyer_intent,
        "source_authority": source_authority, "source_freshness": source_freshness,
        "source_age_days": source_age_days, "owned_source": owned_source,
        "third_party_source": third_party_source, "content_exists": content_exists,
        "factual_conflict": factual_conflict, "persona_value": persona_value,
        "action_cost": action_cost, "historical_success": historical_success,
        "prompt_demand": prompt_demand, "severity": severity, "priority": priority,
        "evidence_confidence": evidence_confidence, "root_cause_confirmed": root_cause_confirmed,
        "platform_breadth": platform_breadth, "memory_similar_n": memory_similar_n,
        "memory_mean_reward": memory_mean_reward, "memory_favorable_rate": memory_favorable_rate,
        "memory_uncertainty": memory_uncertainty, "root_cause_layer": root_cause_layer,
    }
    raw.update({k: v for k, v in explicit.items() if v is not None})
    cat = _category(incident_type) or cat

    vec = np.zeros(DIM, dtype=float)
    missing: list[str] = []
    for name in SCALAR_FEATURES:
        v = _as_float(raw.get(name))
        if name == "source_freshness" and v is None:
            age = _as_float(raw.get("source_age_days"))
            if age is not None:
                v = math.exp(-max(age, 0.0) / FRESHNESS_TAU_DAYS)
        if name == "prompt_volume" and v is None:
            demand = _as_float(raw.get("prompt_demand"))  # already normalised 0..1 (priority component)
            if demand is not None:
                v = demand
        elif name == "prompt_volume" and v is not None:
            v = math.log1p(max(v, 0.0)) / math.log1p(PROMPT_VOLUME_REF)
        if v is None:
            vec[_INDEX[name]] = _NEUTRAL.get(name, 0.0)
            missing.append(name)
        else:
            vec[_INDEX[name]] = _clip(name, v)
    if cat is not None:
        vec[_INDEX[f"incident_type={cat.value}"]] = 1.0
    else:
        missing.append("incident_type")
    vec[_INDEX[BIAS]] = 1.0
    for name in V2_SCALAR_FEATURES:
        v = _as_float(raw.get(name))
        if name == "platform_breadth" and v is not None:
            v = math.log1p(max(v, 0.0)) / math.log1p(PLATFORM_REF)
        if name == "memory_similar_n" and v is not None:
            v = math.log1p(max(v, 0.0)) / math.log1p(MEMORY_N_REF)
        if v is None:
            vec[_INDEX[name]] = _NEUTRAL.get(name, 0.0)
            missing.append(name)
        else:
            vec[_INDEX[name]] = _clip(name, v)
    layer = str(raw.get("root_cause_layer") or "")
    if layer in ROOT_CAUSE_LAYERS:
        vec[_INDEX[f"root_cause={layer}"]] = 1.0
    else:
        missing.append("root_cause_layer")
    return ContextVector(vec, tuple(missing)) if return_details else vec


def incident_type_of(x: np.ndarray) -> IncidentCategory | None:
    for c in INCIDENT_TYPES:
        if x[_INDEX[f"incident_type={c.value}"]] > 0.5:
            return c
    return None


def schema_names(schema: str | None) -> tuple[str, ...]:
    try:
        return FEATURE_SCHEMAS[schema or SCHEMA]
    except KeyError:
        raise ValueError(f"unknown feature schema {schema!r}") from None


def _neutral_for(n: str) -> float:
    return 1.0 if n == BIAS else _NEUTRAL.get(n, 0.0)


def coerce_context(ctx: Any, schema: str | None = None) -> np.ndarray:
    """Accept ndarray / ContextVector / {name: value} / stored JSON {"names","values"} / list and return the vector
    in `schema` (default: the current schema). Named inputs are matched BY NAME (missing -> documented neutral value);
    unnamed vectors must have the dimension of ctx-v1 (padded with neutrals) or of the target schema."""
    names = schema_names(schema)
    dim = len(names)
    if isinstance(ctx, ContextVector):
        x = ctx.values
    elif isinstance(ctx, np.ndarray):
        x = ctx
    elif isinstance(ctx, Mapping):
        if "values" in ctx and "names" in ctx:
            if list(ctx["names"]) != list(names):
                by = dict(zip(ctx["names"], ctx["values"], strict=True))
                x = np.array([by.get(n, _neutral_for(n)) for n in names], dtype=float)
            else:
                x = np.asarray(ctx["values"], dtype=float)
        else:
            x = np.array([ctx.get(n, _neutral_for(n)) for n in names], dtype=float)
    elif isinstance(ctx, Sequence):
        x = np.asarray(ctx, dtype=float)
    else:
        raise TypeError(f"cannot interpret context of type {type(ctx)!r}")
    x = np.asarray(x, dtype=float).reshape(-1)
    if x.shape[0] != dim:
        v1 = len(FEATURE_NAMES_V1)
        if x.shape[0] == v1 and dim > v1:  # legacy ctx-v1 vector -> pad new features with their neutral values
            x = np.concatenate([x, np.array([_neutral_for(n) for n in names[v1:]], dtype=float)])
        elif x.shape[0] > dim and x.shape[0] in SCHEMA_DIMS.values():  # newer vector for an older state: prefix
            x = x[:dim]
        else:
            raise ValueError(f"context has dim {x.shape[0]}, expected {dim}")
    if not np.all(np.isfinite(x)):
        raise ValueError("context contains non-finite values")
    return x
