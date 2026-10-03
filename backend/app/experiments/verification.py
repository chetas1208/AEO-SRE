"""Delayed verification: observations in, reward out - only from measured post-intervention data."""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ExperimentStatus
from app.experiments import window as vwindow
from app.experiments.guards import verification_path
from app.experiments.metrics import jsonable, normalize_metrics
from app.experiments.status import POST_EXECUTION, transition
from app.models.interventions import Experiment, Intervention, Observation, Reward


class ExperimentStateError(Exception):
    pass


class NotEligible(ExperimentStateError):
    """The measurement/attempt is outside the verification window rules (see app.experiments.window)."""

    def __init__(self, eligibility: vwindow.Eligibility):
        self.eligibility = eligibility
        super().__init__(f"{eligibility.code}: {eligibility.reason}")


@dataclass
class EvaluationResult:
    experiment: Experiment
    status: ExperimentStatus
    reward: Reward | None = None
    observation: Observation | None = None
    reason: str = ""
    window_elapsed: bool = False


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _clean(metrics: Any) -> dict[str, Any]:
    return {k: v for k, v in (metrics or {}).items() if not str(k).startswith("_")}


def _load_compute_reward() -> tuple[Callable[..., Any] | None, type[Exception]]:
    try:
        from app.learning import compute_reward
    except ImportError:
        try:
            from app.learning.reward import compute_reward
        except ImportError:
            return None, ValueError
    try:
        from app.learning.reward import NoObservation
    except ImportError:
        NoObservation = ValueError
    return compute_reward, NoObservation


async def record_observation(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    metrics: Any,
    source: str,
    observed_at: datetime | None = None,
    source_run_id: str | None = None,
) -> Observation:
    """Persist a measurement for an executed experiment. Whether it counts is decided in `evaluate`."""
    experiment = await session.get(Experiment, experiment_id)
    if experiment is None:
        raise ExperimentStateError(f"experiment {experiment_id} not found")
    if ExperimentStatus(experiment.status) not in POST_EXECUTION or experiment.dry_run:
        raise ExperimentStateError("observations can only be recorded for a really executed experiment")
    flat = normalize_metrics(metrics)
    if not flat:
        raise ValueError("metrics must contain at least one numeric measurement")
    if not source or not source.strip():
        raise ValueError("source is required")
    obs = Observation(
        id=uuid.uuid4(), experiment_id=experiment_id, metrics=flat, source=source.strip(),
        source_run_id=(source_run_id or "").strip(), observed_at=observed_at or vwindow.now(),
    )
    # Idempotent: the same (experiment, source, source run id, snapshot time) is stored once (DB unique constraint).
    try:
        async with session.begin_nested():
            session.add(obs)
            await session.flush()
    except IntegrityError:
        existing = (await session.execute(select(Observation).where(
            Observation.experiment_id == experiment_id, Observation.source == obs.source,
            Observation.source_run_id == obs.source_run_id, Observation.observed_at == obs.observed_at,
        ))).scalars().first()
        if existing is None:
            raise
        return existing
    return obs


async def apply_measurement(
    session: AsyncSession, experiment: Experiment, metrics: Any, source: str, observed_at: datetime, *,
    run_id: str = "", extra: dict[str, Any] | None = None,
) -> Observation:
    """THE verification path: the only code that sets `Experiment.after_metrics` and moves the experiment to VERIFIED.

    Eligibility is decided by the window service from the measurement's OWN timestamp (`observed_at`, from the
    source) and the injected clock; request time plays no part. Raises `NotEligible` (nothing persisted) otherwise.
    Idempotent: a re-delivered measurement returns the stored observation; an already verified experiment is not
    re-verified."""
    status = ExperimentStatus(experiment.status)
    if status not in (ExperimentStatus.AWAITING_VERIFICATION, ExperimentStatus.VERIFIED, ExperimentStatus.REWARDED):
        raise ExperimentStateError(f"experiment is {status.value}; nothing to verify")
    elig = vwindow.experiment_eligibility(experiment, observed_at)
    if not elig.ok:
        raise NotEligible(elig)
    obs = await record_observation(session, experiment.id, metrics, source, observed_at, run_id)
    if extra:
        obs.metrics = {**obs.metrics, **extra}
    if status == ExperimentStatus.AWAITING_VERIFICATION:
        with verification_path():
            experiment.after_metrics = jsonable(_clean(obs.metrics))
        transition(experiment, ExperimentStatus.VERIFIED, "system",
                   f"post-intervention observation {obs.id}" + (" (after window end)" if elig.late else ""))
        experiment.evaluated_at = vwindow.now()
    await session.flush()
    return obs


async def qualifying_observations(session: AsyncSession, experiment: Experiment,
                                  at: datetime | None = None) -> list[Observation]:
    """Observations eligible under the window service (source timestamp inside/after the window, not future),
    oldest first."""
    if experiment.verification_window_start is None or experiment.executed_at is None:
        return []
    rows = (
        await session.execute(
            select(Observation).where(Observation.experiment_id == experiment.id).order_by(Observation.observed_at)
        )
    ).scalars().all()
    return [o for o in rows if vwindow.experiment_eligibility(experiment, o.observed_at, at=at).ok]


async def evaluate(
    session: AsyncSession,
    experiment: Experiment,
    *,
    weights: Mapping[str, float] | None = None,
    compute_reward: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> EvaluationResult:
    """Verify the experiment iff a qualifying measured observation exists; otherwise leave it waiting.

    Never fabricates: no observation -> status unchanged. Success persists `after_metrics` and moves the experiment
    awaiting_verification -> verified. It does NOT write a Reward (see `ingest_reward`); `compute_reward` is only
    used to check that the observation is usable (comparable to the before-state).
    """
    now = now or vwindow.now()
    status = ExperimentStatus(experiment.status)
    if status == ExperimentStatus.REWARDED:
        existing = (await session.execute(select(Reward).where(Reward.experiment_id == experiment.id))
                    ).scalar_one_or_none()
        return EvaluationResult(experiment, status, existing, None, "already rewarded")
    if experiment.dry_run:
        return EvaluationResult(experiment, status, reason="dry run: nothing was changed, cannot be rewarded")
    if status != ExperimentStatus.AWAITING_VERIFICATION:
        return EvaluationResult(experiment, status, reason=f"experiment is {status.value}, not awaiting verification")

    end = _aware(experiment.verification_window_end)
    elapsed = end is not None and now > end
    candidates = await qualifying_observations(session, experiment, at=now)
    if not candidates:
        return EvaluationResult(experiment, status, reason="no post-intervention observation in/after the "
                                "verification window", window_elapsed=elapsed)
    obs = candidates[-1]

    loaded_fn, no_obs_exc = _load_compute_reward()
    fn = compute_reward or loaded_fn
    if fn is None:
        return EvaluationResult(experiment, status, observation=obs, window_elapsed=elapsed,
                                reason="reward function unavailable (app.learning)")
    intervention = await session.get(Intervention, experiment.intervention_id)
    try:
        result = fn(experiment.before_metrics, obs.metrics, experiment.selected_action, intervention.risk, weights)
        if isawaitable(result):
            result = await result
    except no_obs_exc as exc:
        return EvaluationResult(experiment, status, observation=obs, window_elapsed=elapsed,
                                reason=f"observation not usable for reward: {exc}")

    # Verification stops here. The Reward row, the REWARDED transition and the PolicyVersion are written ONLY by
    # `app.learning.ingest.ingest_reward` (single writer, docs/notes/requests-a9.md).
    with verification_path():
        experiment.after_metrics = jsonable({k: v for k, v in obs.metrics.items() if not str(k).startswith("_")})
    from app.experiments.causal import qualify_association

    raw_confounders = obs.metrics.get("_confounders") if isinstance(obs.metrics, dict) else None
    qual = qualify_association(float(result.total), raw_confounders if isinstance(raw_confounders, list) else [])
    transition(
        experiment, ExperimentStatus.VERIFIED, "system",
        f"verified from observation {obs.id}. {qual['statement']}", now=now,
    )
    experiment.evaluated_at = now
    await session.flush()
    return EvaluationResult(experiment, ExperimentStatus.VERIFIED, None, obs, "verified; awaiting reward ingestion",
                            elapsed)
