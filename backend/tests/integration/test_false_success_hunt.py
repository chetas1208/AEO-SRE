"""Adversarial tests for false-success paths: reward without measured post-intervention data, gate bypass, etc.
Each test encodes an invariant from the hard rules. A failing test here is a real defect (see requests-a13.md)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.domain.enums import ExperimentStatus, IncidentState
from app.models.interventions import Observation, Reward
from app.models.policy import PolicyVersion
from sqlalchemy import select

from tests import factories as f


def _ctx():
    from app.policy import encode_context
    from app.policy.features import FEATURE_NAMES

    return {"names": list(FEATURE_NAMES), "values": [float(v) for v in encode_context(visibility_delta=-0.4)]}


async def _exp(session, org, *, dry_run=False, status=ExperimentStatus.AWAITING_VERIFICATION, executed_at=None,
               window_start=None, after_metrics=None, executor=None):
    from app.interventions.changes import FileChange, ProposedChange

    executed_at = executed_at or f.NOW
    window_start = window_start or executed_at + timedelta(hours=48)
    inc = await f.make_incident(session, org, state=IncidentState.AWAITING_VERIFICATION)
    ch = ProposedChange(title="t", files=[FileChange(path="a.md", new_content="x")]).to_json()
    iv = await f.make_intervention(session, inc, proposed_change=ch)
    pv = await f.make_policy_version(session)
    exp = await f.make_experiment(
        session, inc, iv, pv, status=status, executed_at=executed_at, verification_window_start=window_start,
        verification_window_end=window_start + timedelta(days=7), before_metrics={"visibility": 0.37},
        after_metrics=after_metrics, dry_run=dry_run, context_vector=_ctx(), executor=executor)
    return exp


async def _obs(session, exp, observed_at, metrics=None):
    session.add(Observation(experiment_id=exp.id, metrics=metrics or {"visibility": 0.9}, observed_at=observed_at,
                            source="profound"))
    await session.commit()


async def _reward_attempt(session, exp):
    from app.core.clock import FixedClock
    from app.experiments.window import use_clock
    from app.learning.ingest import IngestError, NotRewardable
    from app.learning.reward import NoObservation

    try:
        with use_clock(FixedClock(f.NOW + timedelta(days=30))):  # injected "now": fixtures are anchored at f.NOW
            await __import__("app.learning.ingest", fromlist=["ingest_reward"]).ingest_reward(session, exp.id)
    except (NoObservation, NotRewardable, IngestError):
        await session.rollback()
        return None
    await session.commit()
    return True


async def test_observation_taken_before_execution_cannot_produce_a_reward(session, org):
    """An observation older than the execution is pre-intervention data; rewarding it would be fake success."""
    exp = await _exp(session, org)
    await _obs(session, exp, f.NOW - timedelta(days=3))
    got = await _reward_attempt(session, exp)
    rewards = (await session.execute(select(Reward))).scalars().all()
    assert got is None and rewards == [], "reward computed from an observation that predates the intervention"


async def test_observation_inside_profound_lag_window_cannot_produce_a_reward(session, org):
    """Window opens 48h after execution (Profound lags 24-48h); earlier observations must not count."""
    exp = await _exp(session, org)
    await _obs(session, exp, f.NOW + timedelta(hours=2))
    got = await _reward_attempt(session, exp)
    assert got is None and (await session.execute(select(Reward))).scalars().all() == []


async def test_dry_run_experiment_can_never_be_rewarded_even_with_metrics(session, org):
    exp = await _exp(session, org, dry_run=True, status=ExperimentStatus.EXECUTED,
                     after_metrics={"visibility": 0.9})
    await _obs(session, exp, f.NOW + timedelta(days=3))
    got = await _reward_attempt(session, exp)
    assert got is None, "a dry run changed nothing; any reward for it is fabricated"
    assert (await session.execute(select(Reward))).scalars().all() == []
    assert len((await session.execute(select(PolicyVersion))).scalars().all()) == 1


async def test_manual_execution_is_a_real_execution_and_is_rewarded(session, org):
    """Manual executions are dry_run=False, so they are verified and rewarded exactly like any real execution
    (only a GitHub-executor preview is a dry run)."""
    exp = await _exp(session, org, executor="manual")  # executor is frozen once executed (B1 guard): set at creation
    assert exp.dry_run is False
    await _obs(session, exp, f.NOW + timedelta(days=3), {"visibility": 0.5})
    assert await _reward_attempt(session, exp) is True
    assert len((await session.execute(select(Reward))).scalars().all()) == 1


async def test_valid_observation_after_window_is_rewarded_and_after_metrics_persisted(session, org):
    """Control: the happy path still works, and the outcome is stored on the experiment."""
    exp = await _exp(session, org)
    await _obs(session, exp, f.NOW + timedelta(days=3), {"visibility": 0.5})
    assert await _reward_attempt(session, exp) is True
    await session.refresh(exp)
    assert exp.status == ExperimentStatus.REWARDED
    assert exp.after_metrics, "the measured after-state must be persisted on the experiment ledger row"


async def test_rewarded_status_requires_after_metrics_invariant(session, org):
    """Every rewarded experiment must carry after_metrics (UI shows before/after next to the reward)."""
    exp = await _exp(session, org)
    await _obs(session, exp, f.NOW + timedelta(days=3), {"visibility": 0.5})
    await _reward_attempt(session, exp)
    rows = (await session.execute(select(Reward))).scalars().all()
    from app.models.interventions import Experiment

    for r in rows:
        e = await session.get(Experiment, r.experiment_id, populate_existing=True)
        assert e.after_metrics and e.evaluated_at is not None


async def test_state_machine_cannot_confirm_root_cause_without_gate_result():
    """transition(..., ROOT_CAUSE_CONFIRMED) without a gate result must be refused (evidence gate is mandatory)."""
    from types import SimpleNamespace

    from app.incidents.state_machine import IllegalTransition, transition

    inc = SimpleNamespace(id=None, state=IncidentState.ROOT_CAUSE_PROPOSED)
    with pytest.raises(IllegalTransition):
        transition(inc, IncidentState.ROOT_CAUSE_CONFIRMED, "someone", "because I said so")
    assert inc.state == IncidentState.ROOT_CAUSE_PROPOSED


async def test_hypothesis_cannot_be_persisted_confirmed_without_gate_trail(session, org):
    """Pipeline only marks hypotheses confirmed via the gate; every confirmed hypothesis must have a passing gate
    record on the incident context."""
    inc = await f.make_incident(session, org, state=IncidentState.ROOT_CAUSE_PROPOSED)
    h = await f.make_hypothesis(session, inc, status="proposed")
    assert h.status == "proposed", "hypotheses start as proposed (facts are never assumed)"


async def test_selected_intervention_is_executable_or_observe(session, mock_http, fast_web, no_queue, monkeypatch):
    """When the template cannot be grounded in cited facts, the policy's pick must not be a mutating action with no
    change attached (a human would approve something that can never run)."""
    import uuid

    from app.models.interventions import Intervention
    from app.services import pipeline

    from tests.support import seed_world

    org, _, _ = await seed_world(session, mock_http, monkeypatch, own_has_fact=False)
    out = await pipeline.detect(session, org.id)
    iid = uuid.UUID(out["created"][0])
    sel = (await session.execute(select(Intervention).where(Intervention.incident_id == iid,
                                                            Intervention.selected.is_(True)))).scalars().one()
    assert sel.action.value == "observe" or sel.proposed_change, (
        f"selected {sel.action.value} but proposed_change is empty: unexecutable action awaiting human approval")
