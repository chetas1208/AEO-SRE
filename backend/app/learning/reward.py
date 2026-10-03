"""Reward from MEASURED pre/post intervention metrics. Never fabricated.

    reward = w_v*vis + w_c*cit + w_a*acc + w_k*comp - action_cost - risk_penalty
    default weights: visibility 0.35, citation 0.30, accuracy 0.20, competitive 0.15

Metric snapshots are dicts of fractions in [0, 1] (values > 1 are read as 0-100 percentages and divided
by 100): `visibility`, `citation_share`, `accuracy`, `competitor_share` (aliases accepted, see
`METRIC_ALIASES`). Each component is the absolute change squashed to [-1, 1]:

    component = tanh((after - before) / DELTA_SCALE)      DELTA_SCALE = 0.10  (+10 points -> +0.76)

`competitive` uses the NEGATED change of competitor_share (a competitor losing presence is good).
`action_cost` (per-action constant, `ACTION_COSTS`) and `risk_penalty` (per-`Risk`, `RISK_PENALTIES`)
are subtracted un-weighted. Total is clipped to [-1, 1].

If a component's metric is missing from `before` or `after` it is reported in `missing` and contributes 0
(weights are NOT redistributed). If `after` is missing/empty or has no comparable metric at all,
`NoObservation` is raised: there is nothing to learn from.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import ActionType, Risk

DELTA_SCALE = 0.10
DEFAULT_WEIGHTS: dict[str, float] = {"visibility": 0.35, "citation": 0.30, "accuracy": 0.20, "competitive": 0.15}
ACTION_COSTS: dict[ActionType, float] = {
    ActionType.OBSERVE: 0.0,
    ActionType.UPDATE_EXISTING_PAGE: 0.03,
    ActionType.CREATE_FAQ: 0.03,
    ActionType.CREATE_CANONICAL_PAGE: 0.06,
    ActionType.CREATE_COMPARISON_CONTENT: 0.06,
    ActionType.PUBLISHER_OUTREACH: 0.05,
    ActionType.STRUCTURED_DATA: 0.02,
}
RISK_PENALTIES: dict[Risk, float] = {Risk.LOW: 0.0, Risk.MEDIUM: 0.03, Risk.HIGH: 0.10}

METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "visibility": ("visibility", "visibility_score", "visibility_pct", "brand_visibility"),
    "citation": ("citation_share", "citation", "citation_rate", "citations"),
    "accuracy": ("accuracy", "answer_accuracy", "factual_accuracy"),
    "competitive": ("competitor_share", "competitor_visibility", "competitor"),
}


class NoObservation(Exception):
    """No measured post-intervention metrics: reward must not be computed."""


@dataclass
class RewardResult:
    components: dict[str, float]
    total: float
    weights: dict[str, float]
    missing: list[str] = field(default_factory=list)
    raw_deltas: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"components": self.components, "total": self.total, "weights": self.weights,
                "missing": self.missing, "raw_deltas": self.raw_deltas}


def _pick(snapshot: Mapping[str, Any] | None, key: str) -> float | None:
    if not snapshot:
        return None
    for alias in METRIC_ALIASES[key]:
        v = snapshot.get(alias)
        if isinstance(v, bool) or v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        return f / 100.0 if abs(f) > 1.0 else f
    return None


def compute_reward(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    action: ActionType | str,
    risk: Risk | str = Risk.LOW,
    weights: Mapping[str, float] | None = None,
) -> RewardResult:
    action, risk = ActionType(action), Risk(risk)
    w = dict(DEFAULT_WEIGHTS if weights is None else weights)
    if not after:
        raise NoObservation("no post-intervention metrics")
    if not before:
        raise NoObservation("no pre-intervention metrics to compare against")
    comps: dict[str, float] = {}
    raw: dict[str, float] = {}
    missing: list[str] = []
    for key in ("visibility", "citation", "accuracy", "competitive"):
        b, a = _pick(before, key), _pick(after, key)
        if b is None or a is None:
            missing.append(key)
            comps[key] = 0.0
            continue
        delta = a - b
        raw[key] = delta
        comps[key] = math.tanh((-delta if key == "competitive" else delta) / DELTA_SCALE)
    if len(missing) == 4:
        raise NoObservation("post-intervention metrics contain no comparable measurement")
    cost = ACTION_COSTS[action]
    penalty = RISK_PENALTIES[risk]
    total = sum(w.get(k, 0.0) * comps[k] for k in comps) - cost - penalty
    total = max(-1.0, min(1.0, total))
    return RewardResult({**comps, "action_cost": cost, "risk_penalty": penalty}, total, w, missing, raw)
