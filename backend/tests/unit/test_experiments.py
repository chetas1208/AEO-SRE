"""Experiment ledger (aiosqlite). Fixtures here are test-only, clearly synthetic."""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import app.models
import pytest
import pytest_asyncio
from app.core.db import Base
from app.domain.enums import ActionType, ExperimentStatus, IncidentCategory, Risk, SelectionBasis
from app.experiments import (
    IllegalExperimentTransition,
    VerificationWindow,
    attach_approval,
    begin_execution,
    can_transition,
    evaluate,
    historical_outcome_range,
    mark_executed,
    open_experiment,
    record_observation,
    transition,
)
from app.experiments.verification import ExperimentStateError
from app.models.core import Incident
from app.models.interventions import Execution, Experiment, Intervention, Observation, Reward
from app.services import approvals as svc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

T0 = datetime(2026, 3, 1, 12, tzinfo=UTC)
BEFORE = {"visibility": 0.37, "citation_share": 0.14, "accuracy": 0.92, "competitor_share": 0.54}
AFTER = {"visibility": 0.48, "citation_share": 0.21, "accuracy": 0.92, "competitor_share": 0.50}


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _incident(session, category=IncidentCategory.VISIBILITY_DROP) -> Incident:
    inc = Incident(id=uuid.uuid4(), org_id=uuid.uuid4(), title="t", category=category.value)
    session.add(inc)
    await session.flush()
    return inc


async def _iv(session, incident, action=ActionType.UPDATE_EXISTING_PAGE) -> Intervention:
    iv = Intervention(
        id=uuid.uuid4(), incident_id=incident.id, action=action, title="x", risk=Risk.LOW,
        proposed_change={"diff": "+a"}, selection_basis=SelectionBasis.COLD_START_PRIOR,
    )
    session.add(iv)
    await session.flush()
    return iv


def _decision(action=ActionType.UPDATE_EXISTING_PAGE):
    return SimpleNamespace(
        action=action, probability=0.62, cold_start=True, policy_version="v0.0.1",
        scores=[{"action": "update_existing_page", "mean": 0.7}, {"action": "observe", "mean": 0.1},
                {"action": "create_faq", "mean": 0.3}],
    )


async def _open(session, incident=None, action=ActionType.UPDATE_EXISTING_PAGE, decision=None):
    incident = incident or await _incident(session)
    iv = await _iv(session, incident, action)
    exp = await open_experiment(session, iv, decision or _decision(action), incident, BEFORE,
                                {"evidence": [{"id": "e1", "hash": "abc"}]}, [0.1, 0.2], now=T0)
    return incident, iv, exp


async def _to_awaiting(session, incident=None, action=ActionType.UPDATE_EXISTING_PAGE, dry_run=False):
    incident, iv, exp = await _open(session, incident, action)
    if action != ActionType.OBSERVE:
        a = await svc.request_approval(session, iv)
        await svc.decide_approval(session, a, "approved", "alice")
        await attach_approval(session, exp, a, now=T0)
    else:
        await attach_approval(session, exp, None, now=T0)
    ex = Execution(id=uuid.uuid4(), intervention_id=iv.id, executor="github_pr", status="succeeded",
                   reference="https://github.com/o/r/pull/1", dry_run=dry_run, finished_at=T0)
    session.add(ex)
    await session.flush()
    await begin_execution(session, exp, ex, now=T0)
    await mark_executed(session, exp, ex, executed_at=T0)
    return incident, iv, exp


# ---------- model ----------

async def test_numbers_unique_and_code(session):
    inc = await _incident(session)
    exps = [(await _open(session, inc))[2] for _ in range(3)]
    assert [e.number for e in exps] == [1, 2, 3]
    assert exps[0].code == "EXP-0001"


# ---------- open_experiment ----------

async def test_open_records_full_state(session):
    _, _, exp = await _open(session)
    assert exp.status == ExperimentStatus.PROPOSED
    assert exp.before_metrics == BEFORE and exp.after_metrics is None
    assert exp.selected_action == ActionType.UPDATE_EXISTING_PAGE
    assert exp.policy_probability == 0.62 and exp.cold_start is True
    assert {a["action"] for a in exp.alternatives} == {"observe", "create_faq"}
    assert exp.evidence_snapshot == {"evidence": [{"id": "e1", "hash": "abc"}]}
    assert exp.context_vector == [0.1, 0.2] and exp.proposed_change == {"diff": "+a"}
    assert exp.verification_window_start == T0 + timedelta(hours=24)
    assert exp.timeline[0]["to"] == "proposed"


async def test_open_requires_before_metrics_and_matching_incident(session):
    inc = await _incident(session)
    iv = await _iv(session, inc)
    with pytest.raises(ValueError):
        await open_experiment(session, iv, _decision(), inc, {}, {}, [])
    with pytest.raises(ValueError):
        await open_experiment(session, iv, _decision(), await _incident(session), BEFORE, {}, [])


async def test_override_drops_propensity(session):
    inc = await _incident(session)
    iv = await _iv(session, inc, ActionType.CREATE_FAQ)
    exp = await open_experiment(session, iv, _decision(ActionType.UPDATE_EXISTING_PAGE), inc, BEFORE, {}, [], now=T0)
    assert exp.policy_probability is None and exp.selection_basis == SelectionBasis.MANUAL_OVERRIDE


# ---------- status machine ----------

def test_transition_table():
    assert can_transition("proposed", "approved")
    assert not can_transition("proposed", "executed")
    assert not can_transition("rewarded", "failed")
    assert not can_transition("awaiting_verification", "rewarded")  # must pass through verified


async def test_illegal_and_guarded_transitions(session):
    _, _, exp = await _open(session)
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.EXECUTED)
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.APPROVED)  # no approval recorded
    assert exp.status == ExperimentStatus.PROPOSED


async def test_reject_path(session):
    _, iv, exp = await _open(session)
    a = await svc.request_approval(session, iv)
    await svc.decide_approval(session, a, "rejected", "alice", "no")
    await attach_approval(session, exp, a)
    assert exp.status == ExperimentStatus.REJECTED and exp.approver == "alice"
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.APPROVED)


async def test_pending_approval_cannot_attach(session):
    _, iv, exp = await _open(session)
    a = await svc.request_approval(session, iv)
    with pytest.raises(ValueError):
        await attach_approval(session, exp, a)


async def test_no_execution_without_human_approval(session):
    _, iv, exp = await _open(session)
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.EXECUTING)
    a = await svc.request_approval(session, iv)
    await svc.decide_approval(session, a, "approved", "alice")
    await attach_approval(session, exp, a)
    # approval row later found not to be human-decided (tampered below the ORM) -> executor gate refuses
    await session.execute(type(a).__table__.update().where(type(a).id == a.id).values(decided_actor_type="model"))
    await session.refresh(a)
    with pytest.raises(svc.ExecutionNotAuthorized):
        await begin_execution(session, exp)


async def test_modified_change_becomes_approved_change(session):
    _, iv, exp = await _open(session)
    a = await svc.request_approval(session, iv)
    await svc.decide_approval(session, a, "modified", "alice", modified_change={"diff": "+human"})
    await attach_approval(session, exp, a)
    assert exp.status == ExperimentStatus.APPROVED
    assert exp.approved_change == {"diff": "+human"} and exp.proposed_change == {"diff": "+a"}


# ---------- verification / reward ----------

async def test_awaiting_without_observation_never_rewards(session):
    _, _, exp = await _to_awaiting(session)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION
    assert exp.execution_reference.endswith("/pull/1")
    res = await evaluate(session, exp, now=T0 + timedelta(days=30))
    assert res.reward is None and res.status == ExperimentStatus.AWAITING_VERIFICATION
    assert res.window_elapsed is True and exp.after_metrics is None and exp.evaluated_at is None
    assert (await session.execute(select(Reward))).scalars().all() == []


async def test_observation_before_window_does_not_count(session):
    _, _, exp = await _to_awaiting(session)
    await record_observation(session, exp.id, AFTER, "profound", observed_at=T0 + timedelta(hours=3))
    res = await evaluate(session, exp, now=T0 + timedelta(hours=4))
    assert res.reward is None and exp.status == ExperimentStatus.AWAITING_VERIFICATION


async def test_observation_in_window_verifies_but_does_not_reward(session):
    """evaluate() only VERIFIES (persists after_metrics); Reward/REWARDED belong to ingest_reward (single writer)."""
    _, _, exp = await _to_awaiting(session)
    await record_observation(session, exp.id, BEFORE, "profound", observed_at=T0 + timedelta(hours=2))
    await record_observation(session, exp.id, AFTER, "profound", observed_at=T0 + timedelta(days=2))
    res = await evaluate(session, exp, now=T0 + timedelta(days=3))
    assert res.status == ExperimentStatus.VERIFIED and res.reward is None
    assert exp.after_metrics == AFTER and exp.evaluated_at is not None
    assert [t["to"] for t in exp.timeline][-2:] == ["awaiting_verification", "verified"]
    assert (await session.execute(select(Reward))).scalars().all() == []
    again = await evaluate(session, exp)  # already verified: no second transition, still no reward
    assert again.reward is None and exp.status == ExperimentStatus.VERIFIED


async def test_observation_after_window_end_still_counts(session):
    _, _, exp = await _to_awaiting(session)
    await record_observation(session, exp.id, AFTER, "profound", observed_at=T0 + timedelta(days=20))
    res = await evaluate(session, exp, now=T0 + timedelta(days=21))
    assert res.status == ExperimentStatus.VERIFIED


async def test_unusable_observation_does_not_reward(session):
    _, _, exp = await _to_awaiting(session)
    await record_observation(session, exp.id, {"unrelated_metric": 5}, "profound", observed_at=T0 + timedelta(days=2))
    res = await evaluate(session, exp, now=T0 + timedelta(days=3))
    assert res.reward is None and exp.status == ExperimentStatus.AWAITING_VERIFICATION
    assert "not usable" in res.reason


async def test_injected_compute_reward_only_gates_usability(session):
    _, _, exp = await _to_awaiting(session)
    await record_observation(session, exp.id, AFTER, "profound", observed_at=T0 + timedelta(days=2))
    calls = []

    def fake(before, after, action, risk, weights):
        calls.append((before, after, action, risk))
        return SimpleNamespace(components={"visibility": 0.5}, total=0.42, weights={"visibility": 1.0})

    res = await evaluate(session, exp, compute_reward=fake, now=T0 + timedelta(days=3))
    assert res.reward is None and res.status == ExperimentStatus.VERIFIED
    assert calls == [(BEFORE, AFTER, ActionType.UPDATE_EXISTING_PAGE, Risk.LOW)]


async def test_dry_run_never_verified(session):
    _, _, exp = await _to_awaiting(session, dry_run=True)
    assert exp.status == ExperimentStatus.EXECUTED and exp.dry_run and exp.verification_window_start is None
    with pytest.raises(ExperimentStateError):
        await record_observation(session, exp.id, AFTER, "profound")
    res = await evaluate(session, exp)
    assert res.reward is None and "dry run" in res.reason


async def test_record_observation_validation(session):
    _, _, exp = await _open(session)
    with pytest.raises(ExperimentStateError):
        await record_observation(session, exp.id, AFTER, "profound")  # not executed yet
    _, _, exp2 = await _to_awaiting(session)
    with pytest.raises(ValueError):
        await record_observation(session, exp2.id, {}, "profound")
    with pytest.raises(ValueError):
        await record_observation(session, exp2.id, AFTER, " ")
    assert (await session.execute(select(Observation))).scalars().all() == []


async def test_observe_action_flows_without_approval(session):
    _, _, exp = await _to_awaiting(session, action=ActionType.OBSERVE)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.approval_id is None


async def test_custom_window(session):
    _, iv, exp = await _open(session)
    a = await svc.request_approval(session, iv)
    await svc.decide_approval(session, a, "approved", "alice")
    await attach_approval(session, exp, a)
    await begin_execution(session, exp)
    await mark_executed(session, exp, None, executed_at=T0, window=VerificationWindow(timedelta(hours=48), timedelta(days=3)))
    assert exp.verification_window_start == T0 + timedelta(hours=48)
    assert exp.verification_window_end == T0 + timedelta(hours=48, days=3)


# ---------- historical_outcome_range ----------

async def _rewarded(session, incident, delta, total, action=ActionType.UPDATE_EXISTING_PAGE):
    _, _, exp = await _to_awaiting(session, incident, action)
    after = {**BEFORE, "visibility": BEFORE["visibility"] + delta}
    await record_observation(session, exp.id, after, "profound", observed_at=T0 + timedelta(days=2))
    def compute(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(components={"visibility": total}, total=total, weights={})

    await evaluate(session, exp, compute_reward=compute, now=T0 + timedelta(days=3))
    # Test fixture standing in for ingest_reward (the single production writer of Reward/REWARDED).
    session.add(Reward(experiment_id=exp.id, components={"visibility": total}, total=total, weights={}))
    transition(exp, ExperimentStatus.REWARDED, "test", "fixture: reward written")
    await session.flush()
    return exp


async def test_outcome_range_none_below_min_n(session):
    inc = await _incident(session)
    for d in (0.05, 0.08, 0.11, 0.09):
        await _rewarded(session, inc, d, 0.3)
    assert await historical_outcome_range(session, IncidentCategory.VISIBILITY_DROP,
                                          ActionType.UPDATE_EXISTING_PAGE) is None
    # awaiting experiments do not count
    await _to_awaiting(session, inc)
    assert await historical_outcome_range(session, "visibility_drop", "update_existing_page") is None


async def test_outcome_range_computed_from_history_only(session):
    inc = await _incident(session)
    for d in (0.05, 0.08, 0.10, 0.12, 0.15):
        await _rewarded(session, inc, d, d * 4)
    other = await _incident(session, IncidentCategory.STALE_INFORMATION)
    await _rewarded(session, other, 0.9, 0.9)  # different class: excluded
    await _rewarded(session, inc, 0.9, 0.9, ActionType.CREATE_FAQ)  # different action: excluded
    out = await historical_outcome_range(session, IncidentCategory.VISIBILITY_DROP, ActionType.UPDATE_EXISTING_PAGE)
    assert out is not None and out.n == 5
    vis = out.metrics["visibility"]
    assert vis.n == 5 and vis.median == pytest.approx(0.10)
    assert vis.low == pytest.approx(0.08) and vis.high == pytest.approx(0.12)
    assert out.reward.n == 5
    assert await historical_outcome_range(session, IncidentCategory.VISIBILITY_DROP,
                                          ActionType.UPDATE_EXISTING_PAGE, min_n=6) is None
