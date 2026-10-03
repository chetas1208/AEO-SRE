"""Expected-outcome helper: historical ranges from REWARDED experiments of the same incident class + action.

Never static. Returns None when there are fewer than `min_n` evaluated experiments (UI: "Insufficient
history"). Deltas are in fraction units (0.08 == +8pp), using the same percentage convention as the reward.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base
from app.domain.enums import ActionType, ExperimentStatus, IncidentCategory
from app.experiments.metrics import as_fraction, normalize_metrics
from app.models.interventions import Experiment, Reward

DEFAULT_MIN_N = 5


def configured_min_n() -> int:
    try:
        return max(1, int(os.environ.get("AEO_OUTCOME_MIN_N", DEFAULT_MIN_N)))
    except ValueError:
        return DEFAULT_MIN_N


@dataclass
class Range:
    low: float
    high: float
    median: float
    n: int


@dataclass
class OutcomeRange:
    n: int
    min_n: int
    metrics: dict[str, Range] = field(default_factory=dict)  # per-metric delta (after - before)
    reward: Range | None = None
    basis: str = "p25-p75 of measured deltas from verified experiments of the same incident class and action"


def _quantile(sorted_vals: list[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _range(values: list[float]) -> Range:
    v = sorted(values)
    return Range(_quantile(v, 0.25), _quantile(v, 0.75), _quantile(v, 0.5), len(v))


async def historical_outcome_range(
    session: AsyncSession,
    incident_category: IncidentCategory | str,
    action: ActionType | str,
    *,
    min_n: int | None = None,
) -> OutcomeRange | None:
    min_n = configured_min_n() if min_n is None else min_n
    category = IncidentCategory(incident_category).value
    action = ActionType(action)
    incidents = Base.metadata.tables.get("incidents")
    if incidents is None:
        return None
    rows = (
        await session.execute(
            select(Experiment, Reward.total)
            .join(incidents, incidents.c.id == Experiment.incident_id)
            .outerjoin(Reward, Reward.experiment_id == Experiment.id)
            .where(
                Experiment.status == ExperimentStatus.REWARDED,
                Experiment.selected_action == action,
                Experiment.dry_run.is_(False),
                Experiment.after_metrics.is_not(None),
                incidents.c.category == category,
            )
        )
    ).all()
    if len(rows) < min_n:
        return None
    deltas: dict[str, list[float]] = {}
    rewards: list[float] = []
    for exp, total in rows:
        before, after = normalize_metrics(exp.before_metrics), normalize_metrics(exp.after_metrics)
        for k in before.keys() & after.keys():
            deltas.setdefault(k, []).append(as_fraction(after[k]) - as_fraction(before[k]))
        if total is not None:
            rewards.append(float(total))
    return OutcomeRange(
        n=len(rows), min_n=min_n,
        metrics={k: _range(v) for k, v in sorted(deltas.items())},
        reward=_range(rewards) if rewards else None,
    )
