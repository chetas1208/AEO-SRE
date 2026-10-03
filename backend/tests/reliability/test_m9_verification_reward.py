"""M9 (V3 audit): delayed verification -> reward -> policy update, and every way it must NOT happen.

DELAYED LOOP = TEST DATA: the post-window Profound observations below are RECORDED/TEST ONLY Signal rows, injected so the
delayed loop can be exercised without waiting 48h. They are never live data. The code under test (verify, reward,
ingest_reward, state machine, policy store) is the real implementation.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from app.domain.enums import ExperimentStatus, IncidentState, Risk
from app.experiments.status import IllegalExperimentTransition, transition
from app.learning.ingest import AlreadyRewarded, IngestError, NotRewardable, ingest_reward
from app.learning.reward import ACTION_COSTS, DEFAULT_WEIGHTS, DELTA_SCALE, RISK_PENALTIES, NoObservation
from app.models.core import Incident
from app.models.interventions import Experiment, Observation, Reward
from app.models.policy import PolicyVersion
from app.services import pipeline
from sqlalchemy import func, select

from tests import factories as f
from tests.reliability.helpers import add_obs, executed_experiment


def _now():
    return datetime.now(UTC)


async def _counts(session):
    r = (await session.execute(select(func.count()).select_from(Reward))).scalar()
    p = (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar()
    o = (await session.execute(select(func.count()).select_from(Observation))).scalar()
    return r, p, o


async def _fresh(session, model, ident):
    return await session.get(model, ident, populate_existing=True)


@pytest.fixture
def no_profound(monkeypatch):
    monkeypatch.delenv("PROFOUND_API_KEY", raising=False)
    monkeypatch.delenv("PROFOUND_BASE_URL", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ------------------------------------------------------------------------- delayed loop (TEST DATA)


@pytest.mark.parametrize(("after_visibility", "verdict"), [(55.0, "favorable"), (20.0, "unfavorable"), (37.0, "neutral")])
async def test_delayed_loop_test_data_verified_then_rewarded_then_policy_updated(
        session, org, no_queue, no_profound, after_visibility, verdict):
    """TEST DATA: executed 3 days ago, window opened 48h after execution, one post-window observation (RECORDED/TEST ONLY).
    A decoy 'observation' inside the lag window (visibility 99) must be ignored."""
    now = _now()
    inc, iv, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    before = dict(exp.before_metrics)
    await f.make_signal(session, org, value=99.0, observed_at=now - timedelta(days=3) + timedelta(hours=2))  # lag decoy
    await f.make_signal(session, org, value=after_visibility, observed_at=now - timedelta(hours=1))
    out = await pipeline.verify(session, exp_id)
    assert out["status"] == "verified" and out["next"], out
    exp = await _fresh(session, Experiment, exp_id)
    assert exp.status == ExperimentStatus.REWARDED
    assert exp.after_metrics == {"visibility": after_visibility}, "the decoy inside the lag window must not be used"
    assert exp.before_metrics == before, "the before-state is never rewritten"
    # lifecycle went through every step, in order, via status.transition (no jumps)
    assert [t["to"] for t in exp.timeline][-2:] == ["verified", "rewarded"]
    rw = (await session.execute(select(Reward).where(Reward.experiment_id == exp_id))).scalars().one()
    if verdict == "favorable":
        assert rw.total > 0 and rw.components["visibility"] > 0
    elif verdict == "unfavorable":
        assert rw.total < 0 and rw.components["visibility"] < 0
    else:  # neutral: no measured movement -> only the action cost remains
        assert rw.components["visibility"] == pytest.approx(0.0) and -0.1 < rw.total <= 0
    assert "citation" not in rw.components, "citation was not measured: the component is absent, not a made-up 0"
    versions = (await session.execute(select(PolicyVersion).order_by(PolicyVersion.n_updates))).scalars().all()
    assert [v.version for v in versions] == ["v0.0.1", "v0.0.2"]
    assert versions[1].source_experiment_id == exp_id and versions[1].parent_id == versions[0].id
    inc = await _fresh(session, Incident, inc.id)
    assert inc.state == IncidentState.REWARDED.value
    assert (await pipeline.verify(session, exp_id))["status"] == "already_rewarded"
    assert await _counts(session) == (1, 2, 1)


# ------------------------------------------------------------------------- never rewarded


async def test_dry_run_experiment_is_never_verified_or_rewarded_by_any_path(session, org, no_queue, no_profound):
    from app.experiments.verification import ExperimentStateError, evaluate, record_observation
    from app.services.pipeline import PermanentError

    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3), dry_run=True,
                                          status=ExperimentStatus.EXECUTED)
    exp_id = exp.id
    await f.make_signal(session, org, value=90.0, observed_at=now - timedelta(hours=1))
    assert (await pipeline.verify(session, exp_id))["status"] == "not_verifiable"
    assert (await pipeline.verify(session, exp_id, force=True))["status"] == "not_verifiable"
    with pytest.raises(ExperimentStateError):
        await record_observation(session, exp_id, {"visibility": 90.0}, "profound")
    with pytest.raises(NotRewardable):
        await ingest_reward(session, exp_id)
    await session.rollback()
    with pytest.raises(PermanentError):
        await pipeline.reward(session, exp_id)
    await session.rollback()
    exp = await _fresh(session, Experiment, exp_id)
    res = await evaluate(session, exp, now=now)
    assert res.reward is None and "dry run" in res.reason
    assert exp.status == ExperimentStatus.EXECUTED and exp.after_metrics is None
    assert await _counts(session) == (0, 1, 0)


async def test_even_a_dry_run_forced_into_awaiting_verification_cannot_be_rewarded(session, org):
    """Belt and braces: if corrupt data put a dry run in awaiting_verification with a valid observation, ingest refuses."""
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3), dry_run=True)
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1))
    with pytest.raises(NotRewardable):
        await ingest_reward(session, exp_id)
    await session.rollback()
    assert await _counts(session) == (0, 1, 1)
    with pytest.raises(IllegalExperimentTransition):  # state machine independently refuses a dry run
        transition(await _fresh(session, Experiment, exp_id), ExperimentStatus.EXECUTED)


async def test_failed_and_rejected_experiments_are_never_rewarded(session, org):
    now = _now()
    for st in (ExperimentStatus.FAILED, ExperimentStatus.REJECTED, ExperimentStatus.PROPOSED, ExperimentStatus.APPROVED,
               ExperimentStatus.EXECUTING):
        await session.refresh(org)  # (rollback below expires it)
        _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3), status=st,
                                              after_metrics={"visibility": 80.0})
        exp_id = exp.id
        await add_obs(session, exp, now - timedelta(hours=1))
        with pytest.raises(NotRewardable):
            await ingest_reward(session, exp_id)
        await session.rollback()
    assert await _counts(session) == (0, 1, 5)


# ------------------------------------------------------------------------- observation gating


async def test_observation_inside_profound_lag_window_is_rejected_even_when_forced(session, org, no_queue, no_profound):
    now = _now()
    inc, iv, exp = await executed_experiment(session, org, executed_at=now - timedelta(hours=5))  # window opens in 43h
    exp_id = exp.id
    await f.make_signal(session, org, value=90.0, observed_at=now - timedelta(hours=1))
    for force in (False, True):
        out = await pipeline.verify(session, exp_id, force=force)
        assert out["status"] == "awaiting_window", (force, out)
    await add_obs(session, exp, now - timedelta(hours=1), {"visibility": 90.0})  # recorded but inside the lag window
    with pytest.raises(NoObservation):
        await ingest_reward(session, exp_id)
    await session.rollback()
    exp = await _fresh(session, Experiment, exp_id)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.after_metrics is None
    assert (await session.execute(select(func.count()).select_from(Reward))).scalar() == 0


async def test_observation_before_execution_and_exactly_at_execution_are_rejected(session, org):
    now = _now()
    t0 = now - timedelta(days=3)
    _, _, exp = await executed_experiment(session, org, executed_at=t0, delay_hours=0)  # window opens AT execution
    exp_id = exp.id
    await add_obs(session, exp_id, t0 - timedelta(days=2), {"visibility": 80.0})  # pre-intervention
    await add_obs(session, exp_id, t0, {"visibility": 80.0})  # the instant of execution is still the before-state
    with pytest.raises(NoObservation):
        await ingest_reward(session, exp_id)
    await session.rollback()
    await add_obs(session, exp_id, t0 + timedelta(seconds=1), {"visibility": 80.0})
    assert (await ingest_reward(session, exp_id)).reward.total != 0


async def test_stale_observation_older_than_window_start_never_wins_over_the_valid_one(session, org):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=5))
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(days=4), {"visibility": 5.0})  # stale: executed+24h < window start
    await add_obs(session, exp, now - timedelta(days=1), {"visibility": 60.0})  # valid
    res = await ingest_reward(session, exp_id)
    exp = await _fresh(session, Experiment, exp_id)
    assert exp.after_metrics == {"visibility": 60.0} and res.reward.total > 0


async def test_observation_belonging_to_another_experiment_cannot_reward_this_one(session, org):
    now = _now()
    _, _, a = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    a_id = a.id
    _, _, b = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    b_id = b.id
    for exp_row in (a, b):  # distinct prompt clusters: overlapping interventions on ONE scope are INCONCLUSIVE by design
        cl = await f.make_prompt_cluster(session, org, topic=f"topic-{exp_row.id}")
        inc_row = await session.get(Incident, exp_row.incident_id)
        inc_row.prompt_cluster_id = cl.id
    await session.commit()
    await add_obs(session, b, now - timedelta(hours=1), {"visibility": 90.0})
    with pytest.raises(NoObservation):
        await ingest_reward(session, a_id)
    await session.rollback()
    res = await ingest_reward(session, b_id)  # B legitimately rewarded; A untouched
    await session.commit()
    assert (await _fresh(session, Experiment, a_id)).status == ExperimentStatus.AWAITING_VERIFICATION
    assert res.policy_version.source_experiment_id == b.id
    assert await _counts(session) == (1, 2, 1)


async def test_duplicate_observations_reward_once_and_update_policy_once(session, org, no_queue, no_profound):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    for h in (3, 2, 1):
        await add_obs(session, exp, now - timedelta(hours=h), {"visibility": 50.0 + h})
    await ingest_reward(session, exp_id)
    await session.commit()
    with pytest.raises(AlreadyRewarded):
        await ingest_reward(session, exp_id)
    await session.rollback()
    assert (await pipeline.reward(session, exp_id))["status"] == "already_rewarded"
    assert (await session.execute(select(func.count()).select_from(Reward))).scalar() == 1
    assert (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar() == 2
    assert (await _fresh(session, Experiment, exp_id)).after_metrics == {"visibility": 51.0}, "latest qualifying wins"


async def test_missing_after_metrics_pending_not_rewarded(session, org, no_queue, no_profound):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    out = await pipeline.verify(session, exp_id)  # no signals at all
    assert out["status"] == "awaiting_observation"
    out = await pipeline.reward(session, exp_id)
    assert out["status"] == "awaiting_observation"
    exp = await _fresh(session, Experiment, exp_id)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.after_metrics is None
    assert await _counts(session) == (0, 1, 0)


async def test_observation_with_no_comparable_metric_does_not_reward(session, org):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1), {"unrelated_metric": 5.0, "_signal_ids": ["x"]})
    with pytest.raises(NoObservation):
        await ingest_reward(session, exp_id)
    await session.rollback()
    assert await _counts(session) == (0, 1, 1)


async def test_profound_unavailable_keeps_experiment_pending(session, org, no_queue, no_profound):
    """No Profound credentials: verification cannot refresh signals; with no post-window data it stays pending."""
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    out = await pipeline.verify(session, exp_id)
    assert out["status"] == "awaiting_observation"
    assert (await _fresh(session, Experiment, exp_id)).status == ExperimentStatus.AWAITING_VERIFICATION
    assert await _counts(session) == (0, 1, 0)


# ------------------------------------------------------------------------- policy / reward legitimacy


async def test_missing_policy_context_blocks_the_update_and_writes_no_reward(session, org):
    """No stored decision context -> the policy cannot be updated honestly, so nothing is written at all."""
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3), context_vector={})
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1))
    with pytest.raises(IngestError):
        await ingest_reward(session, exp_id)
    await session.rollback()
    assert await _counts(session) == (0, 1, 1)
    assert (await _fresh(session, Experiment, exp_id)).status == ExperimentStatus.AWAITING_VERIFICATION


async def test_reward_is_derived_from_measured_components_only(session, org):
    """Only visibility was measured: other components are exactly 0, weights are not redistributed, nothing invented."""
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1), {"visibility": 47.0})
    res = await ingest_reward(session, exp_id)
    assert res.reward.missing == ["citation", "accuracy", "competitive"]
    assert [res.reward.components[k] for k in ("citation", "accuracy", "competitive")] == [0.0, 0.0, 0.0]
    expected = (DEFAULT_WEIGHTS["visibility"] * math.tanh((0.47 - 0.37) / DELTA_SCALE)
                - ACTION_COSTS[exp.selected_action] - RISK_PENALTIES[Risk.LOW])
    assert res.reward.total == pytest.approx(expected)


async def test_verified_and_rewarded_require_after_metrics_in_the_state_machine(session, org):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.VERIFIED)  # no after_metrics
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.REWARDED)  # jump awaiting_verification -> rewarded
    from app.experiments.guards import UnverifiedAfterMetrics, verification_path

    with pytest.raises(UnverifiedAfterMetrics):  # no bypass: only the verification path may set after_metrics
        exp.after_metrics = {"visibility": 50.0}
    with verification_path():
        exp.after_metrics = {"visibility": 50.0}
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.REWARDED)  # still a jump: must pass through verified
    transition(exp, ExperimentStatus.VERIFIED)
    transition(exp, ExperimentStatus.REWARDED)
    for dst in ExperimentStatus:
        with pytest.raises(IllegalExperimentTransition):
            transition(exp, dst)  # rewarded is terminal


async def test_policy_version_source_experiment_id_is_unique(session, org):
    """Model-level uniqueness (V1 owns the migration check): two versions cannot claim the same source experiment."""
    from sqlalchemy.exc import IntegrityError

    assert PolicyVersion.__table__.c.source_experiment_id.unique is True
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1))
    await ingest_reward(session, exp_id)
    await session.commit()
    session.add(PolicyVersion(version="v9.9.9", algorithm="linucb", state={}, priors={}, n_updates=2,
                              source_experiment_id=exp_id))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_verification_evaluate_is_not_a_second_reward_writer(session, org):
    """Single writer: experiments.verification.evaluate only VERIFIES; ingest_reward then writes Reward+PolicyVersion."""
    from app.experiments.verification import evaluate

    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await add_obs(session, exp, now - timedelta(hours=1), {"visibility": 50.0})
    res = await evaluate(session, exp, now=now)
    await session.commit()
    assert res.status == ExperimentStatus.VERIFIED and res.reward is None
    assert await _counts(session) == (0, 1, 1), "evaluate() must not write a Reward"
    out = await ingest_reward(session, exp_id)
    await session.commit()
    assert out.reward.total > 0 and (await _fresh(session, Experiment, exp_id)).status == ExperimentStatus.REWARDED
    assert await _counts(session) == (1, 2, 1)
