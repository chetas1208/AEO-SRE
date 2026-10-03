"""Experiment declaration: IF action X BECAUSE root cause Y THEN primary metric Z SHOULD change AFTER window W.

Declared when the experiment is opened (status `proposed`) and frozen when it leaves `proposed` (B1's ORM guard on
`Experiment.spec`). Metrics are chosen deterministically from what was ACTUALLY measured for this incident
(`before_metrics`); a metric that was not measured can never be declared primary. This is a prediction to be
checked, never evidence that the action works.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import ActionType

# canonical metric key (as stored in before/after_metrics) -> (reward component, expected direction under success)
METRICS: dict[str, tuple[str, str]] = {
    "visibility": ("visibility", "increase"),
    "citation_share": ("citation", "increase"),
    "accuracy": ("accuracy", "increase"),
    "competitor_share": ("competitive", "decrease"),
}
_ALIASES = {
    "visibility": ("visibility", "visibility_score", "visibility_pct", "brand_visibility"),
    "citation_share": ("citation_share", "citation", "citation_rate", "citations"),
    "accuracy": ("accuracy", "answer_accuracy", "factual_accuracy"),
    "competitor_share": ("competitor_share", "competitor_visibility", "competitor"),
}
# default primary metric by incident category (first one measured wins); deterministic, hand-set, not learned
_PRIMARY_BY_CATEGORY = {
    "visibility_drop": ("visibility", "citation_share"),
    "competitor_citation_gain": ("competitor_share", "citation_share", "visibility"),
    "factual_conflict": ("accuracy", "citation_share", "visibility"),
    "lost_citation_source": ("citation_share", "visibility"),
    "prompt_volume_spike": ("visibility", "citation_share"),
    "new_competitor_content": ("competitor_share", "visibility"),
    "stale_information": ("accuracy", "citation_share", "visibility"),
}
REQUIRED = ("if_action", "because_root_cause", "then_metric", "direction", "window_hours", "primary_metric",
            "secondary_metrics", "declared_at")


class SpecError(ValueError):
    pass


def canonical_metric(name: str) -> str | None:
    n = str(name).lower()
    return next((k for k, aliases in _ALIASES.items() if n in aliases), None)


def metric_value(snapshot: Mapping[str, Any] | None, canonical: str) -> float | None:
    """Value of a canonical metric in a snapshot (aliases accepted), as a fraction (|v|>1 read as a percentage)."""
    for alias in _ALIASES.get(canonical, (canonical,)):
        v = (snapshot or {}).get(alias)
        if isinstance(v, bool) or v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):
            continue
        return f / 100.0 if abs(f) > 1.0 else f
    return None


def available_metrics(snapshot: Mapping[str, Any] | None) -> list[str]:
    return [k for k in METRICS if metric_value(snapshot, k) is not None]


@dataclass(frozen=True)
class ExperimentSpec:
    if_action: str
    because_root_cause: str
    then_metric: str
    direction: str
    window_hours: float
    primary_metric: str
    secondary_metrics: tuple[str, ...]
    declared_at: str
    statement: str
    observe: bool = False
    delay_hours: float = 0.0

    def to_json(self) -> dict[str, Any]:
        d = {"if_action": self.if_action, "because_root_cause": self.because_root_cause,
             "then_metric": self.then_metric, "direction": self.direction, "window_hours": self.window_hours,
             "primary_metric": self.primary_metric, "secondary_metrics": list(self.secondary_metrics),
             "declared_at": self.declared_at, "statement": self.statement, "observe": self.observe,
             "delay_hours": self.delay_hours}
        d["spec_hash"] = spec_hash(d)
        return d


def spec_hash(spec: Mapping[str, Any]) -> str:
    body = {k: spec[k] for k in REQUIRED if k in spec}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def choose_primary(category: str | None, before: Mapping[str, Any]) -> str | None:
    have = available_metrics(before)
    for k in _PRIMARY_BY_CATEGORY.get(str(category), ("visibility", "citation_share", "accuracy", "competitor_share")):
        if k in have:
            return k
    return have[0] if have else None


def build_spec(
    *, action: ActionType | str, root_cause: str | None, category: str | None, before_metrics: Mapping[str, Any],
    window_start: datetime, window_end: datetime | None, executed_after: timedelta | None = None,
    declared_at: datetime | None = None,
) -> ExperimentSpec:
    action = ActionType(action)
    primary = choose_primary(category, before_metrics)
    if primary is None:
        raise SpecError("no measurable primary metric in the before-state; an experiment cannot be declared")
    secondary = tuple(m for m in available_metrics(before_metrics) if m != primary)
    direction = METRICS[primary][1]
    duration_h = float(((window_end or window_start) - window_start).total_seconds() / 3600.0)
    # The window W is "verification delay + measurement duration" counted from execution (see window.py).
    delay_h = (executed_after.total_seconds() / 3600.0) if executed_after is not None else 0.0
    window_hours = delay_h + duration_h
    cause = (root_cause or "no root cause established (unconfirmed)").strip()
    observe = action is ActionType.OBSERVE
    verb = ("OBSERVE without changing anything (monitoring experiment)" if observe
            else f"apply {action.value}")
    statement = (f"IF we {verb} BECAUSE {cause} THEN {primary} SHOULD {direction} "
                 f"AFTER {delay_h:g}h from execution (measured within a {window_hours:g}h verification window)")
    if observe:
        statement += " (no change is made; this measures whether the problem recovers on its own)"
    return ExperimentSpec(
        if_action=action.value, because_root_cause=cause, then_metric=primary, direction=direction,
        window_hours=window_hours, primary_metric=primary, secondary_metrics=secondary,
        declared_at=(declared_at or datetime.now(UTC)).isoformat(), statement=statement, observe=observe,
        delay_hours=delay_h)


def validate_spec(spec: Mapping[str, Any] | None, *, before_metrics: Mapping[str, Any] | None = None) -> None:
    """Raise SpecError unless the declaration is complete and its primary metric was actually measured."""
    if not spec:
        raise SpecError("experiment has no declared hypothesis/metric spec")
    missing = [k for k in REQUIRED if spec.get(k) in (None, "", []) and k != "secondary_metrics"]
    if missing:
        raise SpecError(f"spec incomplete: {', '.join(missing)}")
    if spec["primary_metric"] not in METRICS:
        raise SpecError(f"unknown primary metric {spec['primary_metric']!r}")
    if float(spec["window_hours"]) <= 0:
        raise SpecError("spec window_hours must be positive")
    if before_metrics is not None and metric_value(before_metrics, spec["primary_metric"]) is None:
        raise SpecError("primary metric was not measured in the before-state")
    if spec.get("spec_hash") and spec["spec_hash"] != spec_hash(spec):
        raise SpecError("spec_hash does not match its content")
