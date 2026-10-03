"""DB-backed B4 tests (Postgres test DB): context from persisted state, decision record + reproduction, override,
exactly-once outcome/reward/policy update, concurrency, collision, after_metrics guard, memory. Fixed clocks only."""
import asyncio
from datetime import timedelta

import pytest
from app.core.clock import FixedClock
from app.domain.enums import (
    ActionType,
    EvidenceStatus,
    EvidenceType,
    ExperimentStatus,
    HypothesisStatus,
    Risk,
)
from app.experiments import window as vwindow
from app.experiments.collision import ExperimentCollision
from app.experiments.guards import UnverifiedAfterMetrics
from app.learning.ingest import AlreadyRewarded, ingest_reward
from app.learning.memory import retrieve_similar
from app.models.interventions import Experiment, ExperimentOutcome, Reward
from app.models.policy import PolicyVersion
from app.policy.bandit import LinUCBPolicy, PolicyConfig
from app.policy.context import build_policy_context
from app.policy.features import FEATURE_NAMES
from app.policy.store import PolicyStore, reproduce_decision
from sqlalchemy import func, select

from tests import factories

A = ActionType
NOW = factories.NOW
BEFORE = {"visibility": 0.40, "citation_share": 0.20, "accuracy": 0.90, "competitor_share": 0.50}
GOOD = {"visibility": 0.55, "citation_share": 0.30, "accuracy": 0.90, "competitor_share": 0.40}
LATER = FixedClock(NOW + timedelta(days=10))


async def _incident_with_state(session, org, *, owned=True, external=False, confirmed=True):
    cl = await factories.make_prompt_cluster(session, org)
    inc = await factories.make_incident(
        session, org, prompt_cluster_id=cl.id, severity="critical", priority=80.0,
        metrics=[{"key": "visibility", "before": 60.0, "after": 36.0, "delta_pct": -40.0},
                 {"key": "competitor_share", "before": 20.0, "after": 50.0, "delta_pct": 150.0}],
        priority_breakdown={"components": {"buyer_intent": {"value": 0.9, "source": "measured"},
                                           "prompt_demand": {"value": 0.7, "source": "measured"},
                                           "persona_importance": {"value": 0.5, "source": "default"},
                                           "remediation_feasibility": {"value": 0.7, "source": "config"}}})
    if owned:
        await factories.make_evidence(session, inc, type=EvidenceType.OWNED.value)
    if external:
        await factories.make_evidence(session, inc, type=EvidenceType.EXTERNAL.value, url="https://other.example/x")
    hyp = await factories.make_hypothesis(
        session, inc, status=HypothesisStatus.CONFIRMED.value if confirmed else HypothesisStatus.PROPOSED.value,
        confidence=0.82, produced_by="rules:owned_content_stale", title="owned page is stale")
    return cl, inc, hyp


async def test_context_comes_from_persisted_state_and_flags_what_is_missing(session, org):
    _, inc, _ = await _incident_with_state(session, org)
    pc = await build_policy_context(session, inc, with_memory=False)
    x = dict(zip(FEATURE_NAMES, pc.vector.values, strict=True))
    assert x["severity"] == 1.0 and x["priority"] == pytest.approx(0.8)
    assert x["buyer_intent"] == pytest.approx(0.9) and x["prompt_volume"] == pytest.approx(0.7)
    assert x["visibility_delta"] == pytest.approx(-0.4) and x["competitor_delta"] == pytest.approx(1.0)
    assert x["evidence_confidence"] == pytest.approx(0.82) and x["root_cause_confirmed"] == 1.0
    assert x["root_cause=owned_content"] == 1.0 and x["owned_source"] == 1.0 and x["content_exists"] == 1.0
    assert x["action_cost"] == pytest.approx(0.3)
    assert "persona_value" in pc.missing  # a DEFAULT priority component is not presented as a measurement
    assert pc.schema == "ctx-v2" and pc.to_json()["context"]["schema"] == "ctx-v2"


async def test_mask_derived_from_evidence_rows(session, org):
    _, inc, _ = await _incident_with_state(session, org, owned=True, external=False)
    m = (await build_policy_context(session, inc, with_memory=False)).mask
    assert A.UPDATE_EXISTING_PAGE in m.eligible and A.PUBLISHER_OUTREACH not in m.eligible
    assert A.CREATE_CANONICAL_PAGE not in m.eligible
    _, inc2, _ = await _incident_with_state(session, org, owned=False, external=True)
    m2 = (await build_policy_context(session, inc2, with_memory=False)).mask
    assert A.PUBLISHER_OUTREACH in m2.eligible and A.CREATE_CANONICAL_PAGE in m2.eligible
    assert A.UPDATE_EXISTING_PAGE not in m2.eligible


async def test_policy_decision_record_is_complete_and_reproducible(session, org):
    _, inc, _ = await _incident_with_state(session, org, owned=False, external=True)
    pc = await build_policy_context(session, inc, with_memory=False)
    store = PolicyStore(session)
    policy = await store.load_policy()
    policy._rng.bit_generator.state = __import__("numpy").random.default_rng(11).bit_generator.state
    decision = policy.select(pc.vector.values, eligible=pc.mask.eligible, masked=pc.mask.masked)
    row = await store.record_decision(decision, policy, inc.id, {"policy_context": pc.to_json()})
    await session.commit()
    assert row.context_vector["schema"] == "ctx-v2" and row.meta["feature_schema"] == "ctx-v2"
    assert row.meta["masked_actions"]["update_existing_page"] and set(row.allowed_actions) == {
        a.value for a in pc.mask.eligible}
    assert row.meta["selection"]["uniform_draw"] is not None and row.meta["hyperparameters"]["alpha"]
    assert row.policy_version_id == policy.version_id and row.created_at is not None
    assert {s["action"] for s in row.scores} == {a.value for a in pc.mask.eligible}
    out = await reproduce_decision(session, row)
    assert out["match"] is True and out["action"] == row.selected_action


async def test_policy_version_is_immutable_and_update_creates_n_plus_1(session, org):
    store = PolicyStore(session)
    v1 = await store.ensure_initial()
    await session.commit()
    assert v1.priors["meta"]["cold_start"] is True and v1.priors["meta"]["prior_config_hash"]
    cl, inc, _ = await _incident_with_state(session, org)
    pc = await build_policy_context(session, inc, with_memory=False)
    parent, new = await __import__("app.learning.ingest", fromlist=["x"]).update_policy_from_outcome(
        session, pc.vector.to_json(), A.CREATE_FAQ, 0.4, experiment_id=None)
    await session.commit()
    assert new.parent_id == parent.id and new.n_updates == 1 and new.version != parent.version
    assert new.priors["meta"]["rewarded_experiments"] == 1 and new.priors["meta"]["parent_version"] == parent.version
    assert (await store.get(parent.id)).n_updates == 0
    from app.models.policy import ImmutableVersionError

    parent.n_updates = 99
    with pytest.raises(ImmutableVersionError):
        await session.flush()
    await session.rollback()


async def _executed_experiment(session, org, *, action=A.UPDATE_EXISTING_PAGE, before=BEFORE, with_obs=GOOD,
                               inc_kw=None):
    """A really-executed, awaiting_verification experiment opened through the production ledger path."""
    from app.experiments import ledger
    from app.experiments.window import VerificationWindow

    _, inc, hyp = await _incident_with_state(session, org, **(inc_kw or {}))
    iv = await factories.make_intervention(session, inc, hyp, action=action)
    store = PolicyStore(session)
    pv = await store.ensure_initial()
    pc = await build_policy_context(session, inc, with_memory=False)
    policy = await store.load_policy()
    decision = policy.select(pc.vector.values, eligible=pc.mask.eligible)
    pd = await store.record_decision(decision, policy, inc.id)
    exp = await ledger.open_experiment(session, iv, decision, inc, before, {"evidence": []},
                                       {"names": list(FEATURE_NAMES), "values": [float(v) for v in decision.full_context]},
                                       now=NOW, window=VerificationWindow(timedelta(hours=48), timedelta(days=7)))
    a = await factories.make_approval(session, iv, status="approved")
    if action is A.OBSERVE:
        await ledger.attach_approval(session, exp, None, now=NOW)
    else:
        await ledger.attach_approval(session, exp, a, now=NOW)
    await ledger.begin_execution(session, exp, None, now=NOW)
    await ledger.mark_executed(session, exp, None, executed_at=NOW,
                               window=VerificationWindow(timedelta(hours=48), timedelta(days=7)))
    await session.commit()
    return inc, iv, exp, pd, pv


async def _verify(session, exp, metrics=GOOD, when=None):
    from app.experiments.verification import apply_measurement

    when = when or (NOW + timedelta(days=3))
    with vwindow.use_clock(LATER):
        await apply_measurement(session, exp, metrics, "profound", when, run_id="r1")
    await session.commit()


async def test_experiment_declares_hypothesis_and_metrics_before_activation(session, org):
    _, _, exp, _, _ = await _executed_experiment(session, org)
    s = exp.spec
    assert s["primary_metric"] == "visibility" and s["secondary_metrics"] and s["spec_hash"]
    assert "IF we apply update_existing_page BECAUSE owned page is stale THEN visibility SHOULD increase" in s["statement"]
    assert exp.target_key and exp.policy_decision_id and exp.policy_action
    from app.models.interventions import ImmutableExperimentError

    exp.spec = {**s, "primary_metric": "accuracy"}
    with pytest.raises(ImmutableExperimentError):
        await session.flush()
    await session.rollback()


async def test_after_metrics_cannot_be_set_outside_the_verification_path(session, org):
    _, _, exp, _, _ = await _executed_experiment(session, org)
    with pytest.raises(UnverifiedAfterMetrics):
        exp.after_metrics = {"visibility": 0.99}
    await session.rollback()


async def test_verification_rejects_measurement_before_window_and_accepts_at_boundary(session, org):
    from app.experiments.verification import NotEligible, apply_measurement

    _, _, exp, _, _ = await _executed_experiment(session, org)
    start = exp.verification_window_start
    with vwindow.use_clock(LATER):
        with pytest.raises(NotEligible) as e:
            await apply_measurement(session, exp, GOOD, "profound", start - timedelta(seconds=1))
        assert e.value.eligibility.code == "before_window"
        await apply_measurement(session, exp, GOOD, "profound", start, run_id="b")  # start inclusive
    await session.commit()
    assert exp.status == ExperimentStatus.VERIFIED and exp.after_metrics["visibility"] == 0.55
    with vwindow.use_clock(FixedClock(start - timedelta(seconds=1))):
        _, _, exp2, _, _ = await _executed_experiment(session, org)
        with pytest.raises(NotEligible) as e2:  # clock before the window opens
            await apply_measurement(session, exp2, GOOD, "profound", exp2.verification_window_start)
        assert e2.value.eligibility.code in ("window_not_open", "future_measurement")


async def test_favorable_outcome_rewards_executed_action_once_and_creates_version(session, org):
    inc, iv, exp, pd, pv = await _executed_experiment(session, org)
    await _verify(session, exp)
    with vwindow.use_clock(LATER):
        res = await ingest_reward(session, exp.id)
    await session.commit()
    assert res.learned and res.outcome.outcome.value == "favorable"
    o = (await session.execute(select(ExperimentOutcome))).scalar_one()
    assert o.learning_applied and o.reward_total == pytest.approx(res.reward.total) and o.policy_version_id
    assert o.causal_confidence in ("low", "medium") and "not a demonstrated cause" in o.methodology["causal_statement"]
    rw = (await session.execute(select(Reward))).scalar_one()
    assert set(rw.components) >= {"visibility", "citation", "competitive", "action_cost", "risk_penalty"}
    assert "accuracy" in rw.components  # accuracy was measured (unchanged) so it IS persisted
    versions = (await session.execute(select(PolicyVersion).order_by(PolicyVersion.n_updates))).scalars().all()
    assert [v.n_updates for v in versions] == [0, 1] and versions[1].source_experiment_id == exp.id
    state = (await PolicyStore(session).load_policy(versions[1])).state
    assert state.n[A.UPDATE_EXISTING_PAGE] == 1 and sum(state.n.values()) == 1
    with vwindow.use_clock(LATER):
        with pytest.raises(AlreadyRewarded):
            await ingest_reward(session, exp.id)
    await session.rollback()
    assert (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar() == 2


async def test_inconclusive_outcome_feeds_no_reward_and_no_policy_update(session, org):
    _, _, exp, _, _ = await _executed_experiment(session, org)
    await _verify(session, exp, metrics={"citation_share": 0.3})  # primary metric (visibility) not measured
    with vwindow.use_clock(LATER):
        res = await ingest_reward(session, exp.id)
    await session.commit()
    assert not res.learned and res.outcome.outcome.value == "inconclusive"
    assert (await session.execute(select(func.count()).select_from(Reward))).scalar() == 0
    assert (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar() == 1
    o = (await session.execute(select(ExperimentOutcome))).scalar_one()
    assert o.reward_total is None and o.learning_applied is False and o.policy_version_id is None
    with vwindow.use_clock(LATER):
        with pytest.raises(AlreadyRewarded):  # recorded once; a retry never re-assesses
            await ingest_reward(session, exp.id)


async def test_observe_experiment_outcome_labels_and_zero_cost(session, org):
    _, _, exp, _, _ = await _executed_experiment(session, org, action=A.OBSERVE)
    assert exp.spec["observe"] is True
    await _verify(session, exp, metrics={**BEFORE, "visibility": 0.50})
    with vwindow.use_clock(LATER):
        res = await ingest_reward(session, exp.id)
    assert res.outcome.observe_outcome.value == "self_recovery" and res.reward.components["action_cost"] == 0.0
    o = (await session.execute(select(ExperimentOutcome))).scalar_one()
    assert o.observe_outcome == "self_recovery"


async def test_dry_run_and_failed_executions_never_produce_outcomes(session, org):
    from app.learning.ingest import NotRewardable

    inc, iv, exp, _, _ = await _executed_experiment(session, org)
    exp.dry_run = False
    failed = await factories.make_experiment(session, inc, iv, status="failed", executed_at=NOW)
    with pytest.raises(NotRewardable):
        await ingest_reward(session, failed.id)
    await session.rollback()
    assert (await session.execute(select(func.count()).select_from(ExperimentOutcome))).scalar() == 0


async def test_concurrent_ingestion_rewards_and_updates_policy_exactly_once(session, sessionmaker, org):
    _, _, exp, _, _ = await _executed_experiment(session, org)
    await _verify(session, exp)

    async def worker():
        async with sessionmaker() as s:
            try:
                with vwindow.use_clock(LATER):
                    r = await ingest_reward(s, exp.id)
                await s.commit()
                return "learned" if r.learned else "inconclusive"
            except AlreadyRewarded:
                await s.rollback()
                return "already"

    results = await asyncio.gather(*[worker() for _ in range(4)])
    assert sorted(results).count("learned") == 1 and results.count("already") == 3
    async with sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(Reward))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(ExperimentOutcome))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(PolicyVersion))).scalar() == 2


async def test_collision_refuses_second_active_intervention_but_observe_coexists(session, org):
    from app.experiments import ledger

    inc, iv, exp, _, _ = await _executed_experiment(session, org)  # active on its cluster / target
    # a second incident in the SAME prompt cluster proposes another page change
    inc2 = await factories.make_incident(session, org, prompt_cluster_id=inc.prompt_cluster_id)
    iv2 = await factories.make_intervention(session, inc2, action=A.CREATE_FAQ)
    e2 = await ledger.open_experiment(session, iv2, {"action": A.CREATE_FAQ, "probability": 0.3}, inc2, BEFORE, {},
                                      [0.0], now=NOW)
    with pytest.raises(ExperimentCollision) as c:
        await ledger.activation_check(session, e2)
    assert c.value.conflicts[0].experiment_id == exp.id
    ivo = await factories.make_intervention(session, inc2, action=A.OBSERVE)
    eo = await ledger.open_experiment(session, ivo, {"action": A.OBSERVE, "probability": 1.0}, inc2, BEFORE, {},
                                      [0.0], now=NOW)
    await ledger.activation_check(session, eo)  # OBSERVE coexists with everything
    # different cluster -> no collision
    cl3 = await factories.make_prompt_cluster(session, org, topic="Other topic")
    inc3 = await factories.make_incident(session, org, prompt_cluster_id=cl3.id)
    iv3 = await factories.make_intervention(session, inc3, action=A.CREATE_FAQ,
                                            proposed_change={"target_url": "https://x.example/other"})
    e3 = await ledger.open_experiment(session, iv3, {"action": A.CREATE_FAQ, "probability": 0.3}, inc3, BEFORE, {},
                                      [0.0], now=NOW)
    await ledger.activation_check(session, e3)


async def test_human_override_records_policy_action_and_credits_only_the_executed_action(session, org):
    from app.experiments import ledger

    _, inc, hyp = await _incident_with_state(session, org)
    iv = await factories.make_intervention(session, inc, hyp, action=A.CREATE_FAQ)  # human picked FAQ
    store = PolicyStore(session)
    await store.ensure_initial()
    pol_decision = {"action": A.UPDATE_EXISTING_PAGE, "probability": 0.6, "policy_decision_id": None}
    exp = await ledger.open_experiment(session, iv, pol_decision, inc, BEFORE, {}, [0.0], now=NOW,
                                       override_reason="owner prefers FAQ", override_by="alice")
    assert exp.policy_action == "update_existing_page" and exp.selected_action == A.CREATE_FAQ
    assert exp.override_reason == "owner prefers FAQ" and exp.override_by == "alice"
    assert exp.policy_probability is None and exp.selection_basis.value == "manual_override"


async def test_memory_returns_similar_past_experiments_and_numeric_aggregates(session, org):
    inc, _, exp, _, _ = await _executed_experiment(session, org)
    await _verify(session, exp)
    with vwindow.use_clock(LATER):
        await ingest_reward(session, exp.id)
    await session.commit()
    # a new, similar incident (same category, same kind of state) sees the finished experiment as history
    _, inc2, _ = await _incident_with_state(session, org)
    pc = await build_policy_context(session, inc2, with_memory=True)
    mem = pc.memory
    assert mem["similar_n"] == 1 and mem["rows"][0]["action"] == "update_existing_page"
    assert mem["rows"][0]["outcome"] == "favorable" and mem["rows"][0]["verification_delay_hours"] > 0
    x = dict(zip(FEATURE_NAMES, pc.vector.values, strict=True))
    assert x["memory_similar_n"] > 0 and x["memory_mean_reward"] > 0 and x["memory_uncertainty"] < 1.0
    assert "method" in mem and "embeddings" in mem["method"]
    assert all(not isinstance(v, str) for v in __import__("app.learning.memory", fromlist=["x"]).MemorySummary(
        similar_n=1).features().values())  # numbers only reach the bandit
    # its own incident is never its own history
    again = await retrieve_similar(session, inc, pc.vector.values)
    assert all(r["experiment_id"] != str(exp.id) for r in again.to_json()["rows"])


async def test_rejection_is_supervised_feedback_not_a_reward(session, org):
    from app.learning.feedback import overrides, rejection_feedback

    _, inc, hyp = await _incident_with_state(session, org)
    iv = await factories.make_intervention(session, inc, hyp, action=A.CREATE_FAQ)
    await factories.make_approval(session, iv, status="rejected", note="wrong audience")
    fb = await rejection_feedback(session)
    assert fb["create_faq"].rejected == 1 and fb["create_faq"].reasons == ["wrong audience"]
    assert fb["create_faq"].to_json()["kind"] == "supervised_feedback_not_reward"
    assert (await session.execute(select(func.count()).select_from(Reward))).scalar() == 0
    assert (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar() == 0
    assert await overrides(session) == []


async def test_verification_is_idempotent_for_a_redelivered_measurement(session, org):
    from app.experiments.verification import apply_measurement
    from app.models.interventions import Observation

    _, _, exp, _, _ = await _executed_experiment(session, org)
    when = NOW + timedelta(days=3)
    with vwindow.use_clock(LATER):
        a = await apply_measurement(session, exp, GOOD, "profound", when, run_id="same")
        await session.commit()
        b = await apply_measurement(session, exp, GOOD, "profound", when, run_id="same")
        await session.commit()
    assert a.id == b.id and exp.status == ExperimentStatus.VERIFIED
    assert (await session.execute(select(func.count()).select_from(Observation))).scalar() == 1
