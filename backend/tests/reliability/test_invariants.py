"""The ten reliability invariants of AEO SRE (docs/TODO_VERIFICATION_CAMPAIGN.md step 18), each as one named test.

Some invariants have deeper coverage elsewhere (cross-referenced in the docstrings); these tests are the single
place where each one is asserted end to end, with a positive control wherever a negative could pass vacuously.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import get_settings
from app.domain.enums import ActionType, ExperimentStatus, IncidentState, Risk
from app.experiments.status import IllegalExperimentTransition, transition
from app.investigation.evidence_gate import confirm_aeo_root_cause
from app.learning.ingest import NotRewardable, ingest_reward
from app.learning.reward import NoObservation, compute_reward
from app.models.interventions import Execution, Experiment, Reward
from app.models.policy import PolicyDecision, PolicyVersion
from sqlalchemy import select

from tests.reliability.helpers import (
    CHANGE,
    OBSERVE,
    add_obs,
    ctx_json,
    executed_experiment,
    proposed_intervention,
)
from tests.unit.test_evidence_gate import Hyp, full_set, hyp_for, profound

H = {"X-Actor": "alice@testco.example"}


def _now():
    return datetime.now(UTC)


# 1 ---------------------------------------------------------------------------------------------------------
def test_invariant_01_no_confirmation_without_evidence():
    evs = full_set()
    assert confirm_aeo_root_cause(hyp_for(evs), evs).confirmed is True  # positive control: the gate CAN confirm
    assert confirm_aeo_root_cause(hyp_for(evs), []).confirmed is False
    assert confirm_aeo_root_cause(Hyp(evidence_ids=["p1"]), [profound()]).confirmed is False
    assert confirm_aeo_root_cause(Hyp(evidence_ids=["ghost"]), evs).confirmed is False  # cites nothing real
    # and the state machine will not move to ROOT_CAUSE_CONFIRMED without a passing gate result (deeper: unit tests)
    from types import SimpleNamespace

    from app.incidents.state_machine import IllegalTransition
    from app.incidents.state_machine import transition as inc_transition

    inc = SimpleNamespace(id=None, state=IncidentState.ROOT_CAUSE_PROPOSED)
    with pytest.raises(IllegalTransition):
        inc_transition(inc, IncidentState.ROOT_CAUSE_CONFIRMED, "someone", "trust me")


# 2 ---------------------------------------------------------------------------------------------------------
async def test_invariant_02_no_activation_without_valid_human_approval(app_client, session, org):
    from app.services import approvals as appr
    from app.services import pipeline

    inc, iv, exp = await proposed_intervention(session, org)
    ivid, expid = iv.id, exp.id
    with pytest.raises(IllegalExperimentTransition):  # ledger refuses approve/execute with no approval row
        transition(exp, ExperimentStatus.APPROVED)
    with pytest.raises(appr.ExecutionNotAuthorized):  # executor gate refuses with no approval
        await appr.require_executable_approval(session, iv)
    await session.rollback()
    with pytest.raises(appr.ExecutionNotAuthorized):  # the activation entry point refuses too
        await pipeline.execute(session, ivid)
    await session.rollback()
    for actor in ("model", "system"):  # a machine actor cannot approve over HTTP
        r = await app_client.post(f"/api/interventions/{ivid}/approve", headers={"X-Actor": actor})
        assert r.status_code == 403
    assert (await app_client.post(f"/api/interventions/{ivid}/execute", headers=H)).status_code == 409
    assert (await session.execute(select(Execution))).scalars().all() == []
    assert (await session.get(Experiment, expid, populate_existing=True)).status == ExperimentStatus.PROPOSED
    # positive control: a human can
    assert (await app_client.post(f"/api/interventions/{ivid}/approve", headers=H)).status_code == 200
    assert (await session.get(Experiment, expid, populate_existing=True)).status == ExperimentStatus.APPROVED


# 3 ---------------------------------------------------------------------------------------------------------
async def test_invariant_03_dry_run_can_never_reward(session, org):
    """Deeper coverage: test_m9_verification_reward.py (all entry points)."""
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3), dry_run=True)
    eid = exp.id
    await add_obs(session, eid, now - timedelta(hours=1))
    with pytest.raises(NotRewardable):
        await ingest_reward(session, eid)
    await session.rollback()
    assert (await session.execute(select(Reward))).scalars().all() == []


# 4 ---------------------------------------------------------------------------------------------------------
async def test_invariant_04_pre_lag_observations_are_not_outcomes(session, org):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(hours=10))  # lag window open in 38h
    eid = exp.id
    await add_obs(session, eid, now - timedelta(hours=1), {"visibility": 99.0})
    with pytest.raises(NoObservation):
        await ingest_reward(session, eid)
    await session.rollback()
    assert (await session.get(Experiment, eid, populate_existing=True)).after_metrics is None


# 5 ---------------------------------------------------------------------------------------------------------
async def test_invariant_05_unknown_data_is_never_fabricated(app_client, session, org):
    with pytest.raises(NoObservation):
        compute_reward({"visibility": 0.4}, None, ActionType.CREATE_FAQ, Risk.LOW)
    with pytest.raises(NoObservation):
        compute_reward(None, {"visibility": 0.4}, ActionType.CREATE_FAQ, Risk.LOW)
    with pytest.raises(NoObservation):
        compute_reward({"visibility": 0.4}, {"nonsense": 1}, ActionType.CREATE_FAQ, Risk.LOW)
    from app.policy import encode_context

    cv = encode_context(incident_type="visibility_drop", return_details=True)
    assert cv.missing, "inputs that were not supplied are reported as missing, not invented"
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    detail = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert detail["reward"] is None and detail["after_metrics"] in (None, {}) and detail["awaiting_reward"] is True


# 6 ---------------------------------------------------------------------------------------------------------
async def test_invariant_06_decisions_are_reproducible_from_persisted_context_and_version(session):
    from app.policy import encode_context
    from app.policy.store import PolicyStore

    store = PolicyStore(session)
    await store.ensure_initial()
    ctx = encode_context(incident_type="visibility_drop", visibility_delta=-0.4, owned_source=1, content_exists=1,
                         source_age_days=500, prompt_volume=2000)
    decision, row = await store.decide(ctx)
    await session.commit()
    row = await session.get(PolicyDecision, row.id, populate_existing=True)
    ver = await session.get(PolicyVersion, row.policy_version_id)
    replay = await store.load_policy(ver.version)  # the exact immutable version that decided
    again = replay.score(row.context_vector)  # the exact persisted context
    persisted = {s["action"]: s for s in row.scores}
    assert {a.action.value for a in again} == set(persisted)
    for sc in again:
        assert sc.mean == pytest.approx(persisted[sc.action.value]["mean"])
        assert sc.ucb == pytest.approx(persisted[sc.action.value]["ucb"])
    probs = replay.probabilities(row.context_vector)
    assert probs[ActionType(row.selected_action)] == pytest.approx(row.probability)


# 7 ---------------------------------------------------------------------------------------------------------
async def test_invariant_07_experiment_keeps_before_state_and_provenance(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    ivid, expid = iv.id, exp.id
    await app_client.post(f"/api/interventions/{ivid}/approve", headers=H)
    await app_client.post(f"/api/interventions/{ivid}/executed", json={}, headers=H)
    e = await session.get(Experiment, expid, populate_existing=True)
    assert e.before_metrics == {"visibility": 37.0}, "before-state survives approval and execution"
    assert e.evidence_snapshot == {"evidence": [{"id": "e1", "hash": "abc"}]}
    assert e.policy_version_id is not None and e.policy_probability == pytest.approx(0.6)
    assert e.context_vector == ctx_json()
    assert e.proposed_change == CHANGE and e.approved_change == CHANGE
    assert e.approval_id is not None and e.approver == H["X-Actor"] and e.executed_at is not None
    assert [t["to"] for t in e.timeline] == ["proposed", "approved", "executing", "executed", "awaiting_verification"]


# 8 ---------------------------------------------------------------------------------------------------------
async def test_invariant_08_reward_requires_a_legitimate_after_state(session, org):
    now = _now()
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    eid = exp.id
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.REWARDED)
    with pytest.raises(NoObservation):
        await ingest_reward(session, eid)
    await session.rollback()
    assert (await session.execute(select(Reward))).scalars().all() == []
    # positive control: a legitimate post-window observation rewards, and the math is the documented formula
    await add_obs(session, eid, now - timedelta(hours=1), {"visibility": 47.0})
    res = await ingest_reward(session, eid)
    assert res.reward.total == pytest.approx(0.35 * math.tanh(1.0) - 0.03)


# 9 ---------------------------------------------------------------------------------------------------------
async def test_invariant_09_observe_is_always_a_legitimate_action(app_client, session, org):
    from app.policy import PolicyConfig
    from app.policy.bandit import LinUCBPolicy, resolve_allowed_actions
    from app.services import approvals as appr

    assert resolve_allowed_actions({a.value: False for a in ActionType}) == (ActionType.OBSERVE,)
    p = LinUCBPolicy(PolicyConfig(seed=0, allowed_actions=("observe",)))
    from app.policy import encode_context

    d = p.select(encode_context(incident_type="visibility_drop", owned_source=1))
    assert d.action == ActionType.OBSERVE and d.probability > 0
    inc, iv, exp = await proposed_intervention(session, org, action="observe", change=OBSERVE)
    ivid, expid = iv.id, exp.id
    assert appr.requires_approval(iv) is False
    r = await app_client.post(f"/api/interventions/{ivid}/approve", headers=H)  # a human may still acknowledge it
    assert r.status_code == 200
    e = await session.get(Experiment, expid, populate_existing=True)
    assert e.status == ExperimentStatus.AWAITING_VERIFICATION and e.dry_run is False


# 10 --------------------------------------------------------------------------------------------------------
async def test_invariant_10_works_without_optional_executors(monkeypatch, session, org):
    from app.interventions.executor import ExecutionRefused, select_executor
    from app.services import pipeline

    for k in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO"):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    try:
        for a in ActionType:
            assert select_executor(a).name == "manual" and select_executor(a).capability().available
        with pytest.raises(ExecutionRefused):  # explicit GitHub is refused, never silently substituted
            select_executor(ActionType.UPDATE_EXISTING_PAGE, choice="github")
        inc, iv, exp = await proposed_intervention(session, org)
        from app.domain.enums import ApprovalStatus
        from app.experiments import ledger
        from app.services import approvals as appr

        ap = await appr.request_approval(session, iv)
        await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "alice")
        await ledger.attach_approval(session, exp, ap)
        await session.commit()
        out = await pipeline.execute(session, iv.id)
        assert out["status"] == "awaiting_human_execution" and out["executor"] == "manual"
    finally:
        get_settings.cache_clear()
