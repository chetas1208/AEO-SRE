"""Reward computation + delayed-reward ingestion. Metric values here are test fixtures, not real Profound data."""
from __future__ import annotations

import math
from datetime import timedelta

import pytest
from app.domain.enums import ActionType, Risk
from app.learning import DEFAULT_WEIGHTS, NoObservation, compute_reward
from app.learning.ingest import AlreadyRewarded, NotRewardable, ingest_reward
from app.policy import PolicyStore, encode_context

from tests import factories

BEFORE = {"visibility": 0.30, "citation_share": 0.20, "accuracy": 0.80, "competitor_share": 0.40}


def test_default_weights():
    assert DEFAULT_WEIGHTS == {"visibility": 0.35, "citation": 0.30, "accuracy": 0.20, "competitive": 0.15}
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_reward_components_and_total():
    after = {"visibility": 0.40, "citation_share": 0.25, "accuracy": 0.80, "competitor_share": 0.35}
    r = compute_reward(BEFORE, after, ActionType.UPDATE_EXISTING_PAGE, Risk.MEDIUM)
    assert set(r.components) == {"visibility", "citation", "accuracy", "competitive", "action_cost", "risk_penalty"}
    assert r.components["visibility"] == pytest.approx(math.tanh(1.0))
    assert r.components["citation"] == pytest.approx(math.tanh(0.5))
    assert r.components["accuracy"] == 0.0
    assert r.components["competitive"] == pytest.approx(math.tanh(0.5))  # competitor fell => good
    expected = (0.35 * math.tanh(1.0) + 0.30 * math.tanh(0.5) + 0.15 * math.tanh(0.5)
                - r.components["action_cost"] - r.components["risk_penalty"])
    assert r.total == pytest.approx(expected)
    assert r.weights == DEFAULT_WEIGHTS and not r.missing


def test_negative_outcome_and_costs():
    after = {"visibility": 0.20, "citation_share": 0.10, "accuracy": 0.70, "competitor_share": 0.50}
    r = compute_reward(BEFORE, after, ActionType.CREATE_CANONICAL_PAGE, Risk.HIGH)
    assert r.total < 0 and all(r.components[k] < 0 for k in ("visibility", "citation", "accuracy", "competitive"))
    free = compute_reward(BEFORE, BEFORE, ActionType.OBSERVE, Risk.LOW)
    assert free.total == 0.0  # observe, no change, no cost
    assert compute_reward(BEFORE, BEFORE, ActionType.CREATE_FAQ, Risk.LOW).total < 0  # action cost always paid


def test_custom_weights_and_clip():
    after = {"visibility": 1.0, "citation_share": 1.0, "accuracy": 1.0, "competitor_share": 0.0}
    r = compute_reward(BEFORE, after, ActionType.OBSERVE, Risk.LOW, {"visibility": 1.0, "citation": 1.0,
                                                                     "accuracy": 1.0, "competitive": 1.0})
    assert r.total == 1.0


def test_percent_scale_accepted():
    pct = compute_reward({"visibility": 30.0}, {"visibility": 40.0}, ActionType.OBSERVE)
    frac = compute_reward({"visibility": 0.30}, {"visibility": 0.40}, ActionType.OBSERVE)
    assert pct.total == pytest.approx(frac.total)


@pytest.mark.parametrize("after", [None, {}, {"unrelated": 1.0}, {"visibility": None}, {"visibility": float("nan")}])
def test_no_reward_without_observation(after):
    with pytest.raises(NoObservation):
        compute_reward(BEFORE, after, ActionType.UPDATE_EXISTING_PAGE)


def test_no_reward_without_baseline():
    with pytest.raises(NoObservation):
        compute_reward({}, {"visibility": 0.5}, ActionType.OBSERVE)


def test_partial_observation_not_fabricated():
    r = compute_reward(BEFORE, {"visibility": 0.40}, ActionType.OBSERVE)
    assert set(r.missing) == {"citation", "accuracy", "competitive"}
    assert r.components["citation"] == 0.0 and r.total == pytest.approx(0.35 * math.tanh(1.0))


# ---------------------------------------------------------------- ingestion
async def _setup(session, **exp_kw):
    org = await factories.make_org(session)
    inc = await factories.make_incident(session, org)
    iv = await factories.make_intervention(session, inc, action=ActionType.UPDATE_EXISTING_PAGE, risk=Risk.LOW)
    store = PolicyStore(session)
    v1 = await store.ensure_initial()
    ctx = encode_context(None, incident_type="visibility_drop", owned_source=1, content_exists=1,
                         source_age_days=500, prompt_volume=3000)
    from datetime import timedelta

    exp_kw = {"executed_at": factories.NOW, "verification_window_start": factories.NOW + timedelta(hours=48),
              "verification_window_end": factories.NOW + timedelta(days=9), "dry_run": False, **exp_kw}
    exp = await factories.make_experiment(session, inc, iv, v1, context_vector=[float(v) for v in ctx],
                                          before_metrics=BEFORE, **exp_kw)
    return store, v1, iv, exp


async def test_ingest_requires_measured_observation(session):
    store, v1, _, exp = await _setup(session, after_metrics=None)
    with pytest.raises(NoObservation):
        await ingest_reward(session, exp.id)
    await session.rollback()
    assert (await store.latest()).version == "v0.0.1"  # no policy update, nothing fabricated
    from app.models.interventions import Reward
    from sqlalchemy import select

    assert (await session.execute(select(Reward))).first() is None


async def test_ingest_writes_reward_and_new_version(session):
    from datetime import timedelta

    from app.models.interventions import Observation, Reward
    from sqlalchemy import select

    store, v1, _, exp = await _setup(session, after_metrics=None)
    session.add(Observation(experiment_id=exp.id, observed_at=factories.NOW + timedelta(days=3),
                            metrics={"visibility": 0.45, "citation_share": 0.30, "accuracy": 0.85,
                                     "competitor_share": 0.30, "_signal_ids": ["x"]}))
    await session.commit()
    from app.core.clock import FixedClock
    from app.experiments.window import use_clock

    with use_clock(FixedClock(factories.NOW + timedelta(days=10))):  # injected clock: the observation is not "future"
        res = await ingest_reward(session, exp.id)
    await session.commit()
    assert res.policy_version.version == "v0.0.2" and res.policy_version.parent_id == v1.id
    assert res.policy_version.n_updates == 1 and res.policy_version.source_experiment_id == exp.id
    reward = (await session.execute(select(Reward).where(Reward.experiment_id == exp.id))).scalar_one()
    assert reward.total == pytest.approx(res.reward.total) and reward.total > 0
    assert reward.components["visibility"] > 0
    await session.refresh(exp)
    assert exp.status.value == "rewarded" and "_signal_ids" not in exp.after_metrics
    assert exp.after_metrics["visibility"] == 0.45 and [t["to"] for t in exp.timeline][-2:] == ["verified", "rewarded"]
    assert (await store.get("v0.0.1")).n_updates == 0  # parent untouched
    pol = await store.load_policy("v0.0.2")
    assert pol.state.n[ActionType.UPDATE_EXISTING_PAGE] == 1
    with pytest.raises(AlreadyRewarded):
        with use_clock(FixedClock(factories.NOW + timedelta(days=10))):
            await ingest_reward(session, exp.id)
    assert (await store.latest()).version == "v0.0.2"


async def test_ingest_rejects_unexecuted_experiment(session):
    _, _, _, exp = await _setup(session, status="proposed", after_metrics={"visibility": 0.5})
    with pytest.raises(NotRewardable):
        await ingest_reward(session, exp.id)


async def test_ingest_refuses_dry_run_and_premature_observations(session):
    from datetime import timedelta

    from app.models.interventions import Observation

    _, _, _, dry = await _setup(session, dry_run=True, after_metrics={"visibility": 0.9})
    with pytest.raises(NotRewardable):
        await ingest_reward(session, dry.id)
    await session.rollback()
    _, _, _, exp = await _setup(session)
    session.add(Observation(experiment_id=exp.id, metrics={"visibility": 0.9},
                            observed_at=factories.NOW + timedelta(hours=2), source="profound"))  # inside lag window
    await session.commit()
    with pytest.raises(NoObservation):
        await ingest_reward(session, exp.id)


async def test_ingest_adopts_reward_written_by_verification(session):
    """If the verification step already stored a Reward, ingest still writes the PolicyVersion exactly once
    and does not create a second Reward."""
    from app.models.interventions import Reward
    from sqlalchemy import func, select

    store, v1, _, exp = await _setup(session, status="verified", after_metrics={"visibility": 0.45})
    pre = compute_reward(BEFORE, {"visibility": 0.45}, ActionType.UPDATE_EXISTING_PAGE, Risk.LOW)
    session.add(Reward(experiment_id=exp.id, components=pre.components, total=pre.total, weights=pre.weights))
    await session.commit()
    from app.core.clock import FixedClock
    from app.experiments.window import use_clock

    with use_clock(FixedClock(factories.NOW + timedelta(days=10))):  # injected clock: the observation is not "future"
        res = await ingest_reward(session, exp.id)
    await session.commit()
    assert res.policy_version.version == "v0.0.2" and res.reward.total == pytest.approx(pre.total)
    assert (await session.execute(select(func.count()).select_from(Reward))).scalar_one() == 1
    with pytest.raises(AlreadyRewarded):
        await ingest_reward(session, exp.id)
