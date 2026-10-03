"""Delayed-reward ingestion: measured outcome -> Reward row -> NEW immutable PolicyVersion.

Called by the verification worker after an experiment has a measured post-intervention observation.
Idempotent per experiment (a second call raises `AlreadyRewarded`; the policy is never updated twice).
Does not commit; the caller owns the transaction (it flushes).
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.domain.enums import ActionType, ExperimentStatus, Risk
from app.learning.reward import NoObservation, RewardResult
from app.models.policy import PolicyVersion
from app.policy.store import PolicyStore

log = structlog.get_logger()

REWARDABLE = {ExperimentStatus.AWAITING_VERIFICATION, ExperimentStatus.VERIFIED}


class IngestError(Exception):
    pass


class NotRewardable(IngestError):
    pass


class AlreadyRewarded(IngestError):
    pass


@dataclass
class IngestResult:
    """`learned=False` (INCONCLUSIVE outcome): no Reward row, no policy update, `reward`/versions may be None."""

    reward: RewardResult | None
    policy_version: PolicyVersion | None
    parent_version: PolicyVersion | None
    reward_row: Any = None
    outcome: Any = None  # OutcomeAssessment
    outcome_row: Any = None  # ExperimentOutcome
    learned: bool = True


async def update_policy_from_outcome(
    session: AsyncSession,
    context: Any,
    action: ActionType | str,
    reward: float,
    *,
    experiment_id: uuid.UUID | None = None,
    store: PolicyStore | None = None,
) -> tuple[PolicyVersion, PolicyVersion]:
    """(context, action, reward) -> new PolicyVersion derived from the latest. Returns (parent, new)."""
    store = store or PolicyStore(session)
    for attempt in range(3):  # concurrent ingestions race on the unique version string
        parent = await store.ensure_initial()
        try:
            async with session.begin_nested():
                new = await store.save_update(parent, context, action, reward, experiment_id)
            return parent, new
        except IntegrityError:
            log.warning("policy.version_conflict", attempt=attempt)
            if experiment_id is not None and (await session.execute(select(PolicyVersion.version).where(
                    PolicyVersion.source_experiment_id == experiment_id))).first():
                raise AlreadyRewarded(f"experiment {experiment_id} already updated the policy") from None
    raise IngestError("could not persist new policy version after retries")


def _clean(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop bookkeeping keys (pipeline stores `_signal_ids` etc. inside Observation.metrics)."""
    return {k: v for k, v in (metrics or {}).items() if not str(k).startswith("_")}


async def ingest_reward(
    session: AsyncSession, experiment_id: uuid.UUID, *, weights: Mapping[str, float] | None = None
) -> IngestResult:
    """Assess the measured outcome and, only for a legitimate (non-inconclusive) outcome, reward the executed action
    and create PolicyVersion N+1. Exactly once per experiment: row lock + UNIQUE(experiment_outcomes.experiment_id)
    + UNIQUE(rewards.experiment_id) + UNIQUE(policy_versions.source_experiment_id). Never credits the policy-selected
    action when a human executed a different one (the executed action is `Experiment.selected_action`)."""
    from app.experiments import window as vwindow
    from app.experiments.guards import verification_path
    from app.experiments.outcome import assess, detect_confounders
    from app.experiments.status import transition
    from app.experiments.verification import qualifying_observations
    from app.models.interventions import Experiment, ExperimentOutcome, Intervention, Reward

    exp = await session.get(Experiment, experiment_id, with_for_update=True, populate_existing=True)  # exactly-once lock
    if exp is None:
        raise LookupError(f"experiment {experiment_id} not found")
    done = (await session.execute(
        select(PolicyVersion).where(PolicyVersion.source_experiment_id == exp.id))).scalars().first()
    if done is not None:
        raise AlreadyRewarded(f"experiment {experiment_id} already updated policy {done.version}")
    if (await session.execute(select(ExperimentOutcome.id).where(ExperimentOutcome.experiment_id == exp.id))).first():
        raise AlreadyRewarded(f"experiment {experiment_id} already has a recorded outcome")
    status = ExperimentStatus(exp.status)
    if exp.dry_run:
        raise NotRewardable("dry-run experiment changed nothing; it can never be rewarded")
    if status not in REWARDABLE | {ExperimentStatus.REWARDED}:
        raise NotRewardable(f"experiment status {exp.status!r} cannot be rewarded")
    existing = (await session.execute(select(Reward).where(Reward.experiment_id == exp.id))).scalars().first()

    interv = await session.get(Intervention, exp.intervention_id)
    if interv is None:
        raise IngestError("experiment has no intervention; cannot determine risk")
    # The action actually executed (a human override may differ from the policy's pick).
    action = ActionType(exp.selected_action or interv.action)

    # `after` comes ONLY from an eligible measured observation (window service: source timestamp at/after the window
    # start and after execution, not in the future) or from after_metrics already persisted by verification.
    cands = await qualifying_observations(session, exp)
    obs = cands[-1] if cands else None
    after = _clean(exp.after_metrics) if status in (ExperimentStatus.VERIFIED, ExperimentStatus.REWARDED) else {}
    if not after:
        after = next((_clean(o.metrics) for o in reversed(cands) if _clean(o.metrics)), {})
    if not after:
        raise NoObservation("no qualifying post-intervention observation")
    observed_at = obs.observed_at if obs is not None else (exp.evaluated_at or vwindow.now())
    late = bool(exp.verification_window_end and vwindow.aware(observed_at) > vwindow.aware(exp.verification_window_end))
    spec = getattr(exp, "spec", None)
    primary = (spec or {}).get("primary_metric")
    confounders = await detect_confounders(session, exp, before=exp.before_metrics or {}, after=after,
                                           observed_at=observed_at, primary=primary)
    assessment = assess(before=exp.before_metrics or {}, after=after, action=action, risk=Risk(interv.risk),
                        spec=spec, confounders=confounders, late=late, weights=weights)
    now = vwindow.now()
    if exp.after_metrics is None:
        with verification_path():  # legacy rows verified before this service existed: persist the measured after-state
            exp.after_metrics = after
    if status == ExperimentStatus.AWAITING_VERIFICATION:
        transition(exp, ExperimentStatus.VERIFIED, "system", "verified from measured observation", now=now)
    exp.evaluated_at = exp.evaluated_at or now

    outcome_row = ExperimentOutcome(
        experiment_id=exp.id, outcome=assessment.outcome.value,
        observe_outcome=assessment.observe_outcome.value if assessment.observe_outcome else None,
        reward_total=assessment.reward.total if assessment.learn and assessment.reward else None,
        components=assessment.components(), confounders=assessment.confounders,
        causal_confidence=assessment.causal_confidence, learning_applied=assessment.learn,
        observed_at=observed_at, evaluated_at=now,
        methodology={**assessment.methodology, "causal_statement": assessment.causal_statement,
                     "primary_gain": assessment.primary_gain,
                     "reward_weights": assessment.reward.weights if assessment.reward else None})
    if assessment.learn and not exp.context_vector:
        raise IngestError("experiment has no stored context_vector; cannot update the policy")
    result = assessment.reward
    parent = new = row = None
    try:
        async with session.begin_nested():  # outcome + reward + policy version commit or roll back together
            if assessment.learn:
                assert result is not None
                parent, new = await update_policy_from_outcome(
                    session, exp.context_vector, action, result.total, experiment_id=exp.id)
                outcome_row.policy_version_id = new.id
            session.add(outcome_row)
            await session.flush()
            if assessment.learn:
                row = existing
                if row is None:
                    row = Reward(experiment_id=exp.id, components=assessment.components(), total=result.total,
                                 weights=result.weights, computed_at=utcnow())
                    session.add(row)
                await session.flush()
    except IntegrityError:
        raise AlreadyRewarded(f"experiment {experiment_id} already has a recorded outcome") from None

    if not assessment.learn:  # INCONCLUSIVE: no reward row, no policy update, experiment stays VERIFIED
        log.info("ingest.inconclusive", experiment=str(exp.id), reason=assessment.methodology.get("reason"))
        return IngestResult(assessment.reward, None, None, None, assessment, outcome_row, learned=False)
    if ExperimentStatus(exp.status) == ExperimentStatus.VERIFIED:
        transition(exp, ExperimentStatus.REWARDED, "system", "reward computed from measured outcome", now=now)
    await session.flush()
    try:
        from app.core.audit import audit

        await audit(session, "system", "policy-learner", "experiment", exp.id, "policy_updated",
                    {"from": parent.version, "to": new.version, "reward": result.total,
                     "action": action.value, "outcome": assessment.outcome.value})
    except Exception as exc:  # noqa: BLE001 - auditing must not block learning
        log.warning("ingest_reward.audit_failed", error=str(exc))
    return IngestResult(result, new, parent, row, assessment, outcome_row, True)


__all__ = ["ingest_reward", "update_policy_from_outcome", "IngestResult", "NoObservation", "AlreadyRewarded",
           "NotRewardable", "IngestError"]
