"""B1 domain integrity: fingerprint, signal/observation idempotency, state-machine enforcement, experiment
immutability, exactly-once reward/policy under concurrency, rollback atomicity, audit-db. Postgres.

All data is RECORDED/TEST ONLY.
"""
from __future__ import annotations

import asyncio
import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domain.enums import ApprovalStatus, ExperimentStatus, HypothesisStatus, IncidentState
from app.experiments.status import ALLOWED as EXP_ALLOWED
from app.experiments.status import IllegalExperimentTransition
from app.incidents import state_machine as sm
from app.incidents.fingerprint import fingerprint_from_parts
from app.models.core import AuditEvent, ImmutableAuditError, Incident, Signal
from app.models.evidence import Hypothesis, IllegalHypothesisTransition
from app.models.interventions import (
    Approval,
    Experiment,
    ImmutableExperimentError,
    Observation,
    Reward,
)
from app.models.policy import PolicyVersion
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from tests import factories as f
from tests.reliability.helpers import add_obs, executed_experiment

S = IncidentState
SIG = {"signature": {"incident_type": "visibility_drop", "topic": "SSO", "competitors": ["rival.example"]}}


# ------------------------------------------------------------------ (2) fingerprint

def test_fingerprint_is_deterministic_and_discriminating():
    base = dict(org_id="o1", incident_type="visibility_drop", topic="SSO", persona="CISO", platform="chatgpt",
                competitors=["b", "a"], at=datetime(2026, 10, 2, 5, tzinfo=UTC))
    a = fingerprint_from_parts(**base)
    assert a == fingerprint_from_parts(**{**base, "competitors": ["a", "b"], "at": datetime(2026, 10, 2, 23, tzinfo=UTC)})
    for change in ({"org_id": "o2"}, {"incident_type": "factual_conflict"}, {"topic": "MFA"}, {"persona": "CTO"},
                   {"platform": "gemini"}, {"competitors": ["c"]}, {"at": datetime(2026, 10, 3, tzinfo=UTC)}):
        assert fingerprint_from_parts(**{**base, **change}) != a, change


async def test_open_fingerprint_unique_but_terminal_may_repeat(session, org):
    first = await f.make_incident(session, org, context=SIG)
    first_id, first_fp = first.id, first.fingerprint
    assert first_fp and len(first_fp) == 64
    with pytest.raises(IntegrityError):
        await f.make_incident(session, org, context=SIG)
    await session.rollback()
    await session.refresh(org)
    inc = await session.get(Incident, first_id)
    sm.transition(inc, S.DISMISSED, "tester", "not relevant")
    await session.commit()
    again = await f.make_incident(session, org, context=SIG)  # old one is terminal: allowed
    assert again.fingerprint == first_fp


async def test_hand_built_incidents_without_signature_are_not_collapsed(session, org):
    a, b = await f.make_incident(session, org), await f.make_incident(session, org)
    assert a.fingerprint is None and b.fingerprint is None


async def test_second_detect_creates_zero_duplicates_and_persists_fingerprint(session, org):
    from app.incidents.detector import detect_incidents

    await f.make_signal_series(session, org, metric="visibility", values=f.visibility_drop_values())
    first = await detect_incidents(session, org.id, now=f.NOW)
    await session.commit()
    assert len(first) == 1 and first[0].fingerprint
    assert await detect_incidents(session, org.id, now=f.NOW) == []
    await session.commit()
    assert (await session.scalar(select(func.count()).select_from(Incident))) == 1


# ------------------------------------------------------------------ (3) idempotency

async def test_same_signal_ten_times_is_one_logical_signal(session, org):
    from app.connectors.profound.normalize import NormalizedSignal
    from app.services.ingestion import IngestResult, _upsert_signals

    sig = NormalizedSignal(kind="visibility", metric="visibility", value=41.0, observed_at=f.NOW)
    for _ in range(10):
        res = IngestResult(org_id=str(org.id), status="ok")
        await _upsert_signals(session, org.id, [sig], {}, {}, res)
        await session.commit()
    rows = (await session.execute(select(Signal).where(Signal.org_id == org.id))).scalars().all()
    assert len(rows) == 1 and rows[0].idempotency_key


async def test_signal_unique_constraint_blocks_duplicate_key(session, org):
    raw = {"dedupe_key": "abc", "provider_run_id": "run-1"}
    await f.make_signal(session, org, value=1.0, observed_at=f.NOW, raw=raw)
    with pytest.raises(IntegrityError):
        await f.make_signal(session, org, value=1.0, observed_at=f.NOW, raw=raw)
    await session.rollback()
    row = (await session.execute(select(Signal))).scalars().one()
    assert row.provider_run_id == "run-1"
    # same key in another org / source is a different logical signal
    other = await f.make_org(session)
    await f.make_signal(session, other, value=1.0, observed_at=f.NOW, raw={"dedupe_key": "abc"})


async def test_observation_unique_per_source_run_and_snapshot(session, org):
    from app.experiments.verification import record_observation

    _, _, exp = await executed_experiment(session, org, executed_at=datetime.now(UTC) - timedelta(days=3))
    at = datetime.now(UTC) - timedelta(hours=1)
    first = await record_observation(session, exp.id, {"visibility": 50.0}, "profound", at, source_run_id="r1")
    again = await record_observation(session, exp.id, {"visibility": 50.0}, "profound", at, source_run_id="r1")
    await session.commit()
    assert again.id == first.id
    other = await record_observation(session, exp.id, {"visibility": 50.0}, "profound", at, source_run_id="r2")
    assert other.id != first.id
    n = await session.scalar(select(func.count()).select_from(Observation).where(Observation.experiment_id == exp.id))
    assert n == 2
    session.add(Observation(experiment_id=exp.id, metrics={"visibility": 1.0}, observed_at=at, source="profound",
                            source_run_id="r1"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# ------------------------------------------------------------------ (4) state machines

INC_ILLEGAL = [(a, b) for a, b in itertools.product(S, S) if not sm.can_transition(a, b)]


@pytest.mark.parametrize(("src", "dst"), [(a, b) for a, b in INC_ILLEGAL if a != b][:400])
def test_incident_machine_rejects_every_illegal_edge(src, dst):
    class Obj:
        state = src
        id = None

    with pytest.raises(sm.IllegalTransition):
        sm.transition(Obj(), dst, "t", "r")
    assert Obj.state == src


@pytest.mark.parametrize(("src", "dst"), [
    (S.DETECTED, S.REWARDED), (S.DETECTED, S.VERIFIED), (S.AWAITING_VERIFICATION, S.REWARDED),
    (S.EXECUTED, S.VERIFIED), (S.APPROVED, S.REWARDED), (S.CLOSED, S.DETECTED), (S.FAILED, S.INVESTIGATING),
])
def test_named_illegal_incident_transitions(src, dst):
    assert not sm.can_transition(src, dst)


@pytest.mark.parametrize(("src", "dst"), [
    (a, b) for a, b in itertools.product(ExperimentStatus, ExperimentStatus) if b not in EXP_ALLOWED[a]])
def test_experiment_machine_rejects_every_illegal_edge(src, dst):
    from app.experiments.status import transition

    exp = Experiment(status=src, selected_action="observe", timeline=[], after_metrics={"v": 1})
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, dst)
    assert exp.status == src


async def test_orm_rejects_direct_incident_status_assignment(session, org):
    inc = await f.make_incident(session, org)
    inc_id = inc.id
    inc.state = S.REWARDED.value  # arbitrary assignment, bypassing the machine
    with pytest.raises(sm.IllegalTransition):
        await session.commit()
    await session.rollback()
    assert (await session.get(Incident, inc_id, populate_existing=True)).state == S.DETECTED.value


async def test_orm_rejects_direct_experiment_status_jump(session, org):
    _, _, exp = await executed_experiment(session, org)
    exp.status = ExperimentStatus.REWARDED  # awaiting_verification -> rewarded skips verification
    with pytest.raises(IllegalExperimentTransition):
        await session.commit()
    await session.rollback()


async def test_verification_cannot_be_reached_without_measured_metrics():
    from app.experiments.status import transition

    exp = Experiment(status=ExperimentStatus.AWAITING_VERIFICATION, selected_action="observe", timeline=[],
                     after_metrics=None)
    with pytest.raises(IllegalExperimentTransition):
        transition(exp, ExperimentStatus.VERIFIED)


async def test_decided_approval_is_immutable(session, org):
    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    ap = await f.make_approval(session, iv, status="approved")
    ap.status = ApprovalStatus.REJECTED
    with pytest.raises(Exception, match="already"):
        await session.commit()
    await session.rollback()


async def test_hypothesis_transitions_and_confirmation_needs_evidence(session, org):
    inc = await f.make_incident(session, org)
    ev = await f.make_evidence(session, inc)
    ev_id = ev.id
    h = await f.make_hypothesis(session, inc)
    h_id = h.id
    h.status = HypothesisStatus.CONFIRMED.value  # no evidence ids
    with pytest.raises(IllegalHypothesisTransition):
        await session.commit()
    await session.rollback()
    h = await session.get(Hypothesis, h_id, populate_existing=True)
    h.evidence_ids, h.status = [str(ev_id)], HypothesisStatus.CONFIRMED.value
    await session.commit()
    h.status = HypothesisStatus.PROPOSED.value
    with pytest.raises(IllegalHypothesisTransition):
        await session.commit()
    await session.rollback()


# ------------------------------------------------------------------ (5) immutability

@pytest.mark.parametrize(("field", "value"), [
    ("before_metrics", {"visibility": 99.0}), ("evidence_snapshot", {"x": 1}), ("context_vector", [9]),
    ("policy_probability", 0.99), ("selected_action", "create_faq"), ("proposed_change", {"x": 1}),
    ("alternatives", [{"a": 1}]), ("spec", {"s": 1}), ("policy_version_id", uuid.uuid4()),
    ("verification_window_end", datetime(2030, 1, 1, tzinfo=UTC)),
])
async def test_activated_experiment_snapshot_is_frozen(session, org, field, value):
    _, _, exp = await executed_experiment(session, org)
    setattr(exp, field, value)
    with pytest.raises(ImmutableExperimentError):
        await session.commit()
    await session.rollback()


async def test_after_metrics_write_once_and_experiment_never_deleted(session, org):
    from app.experiments.guards import verification_path

    _, _, exp = await executed_experiment(session, org)
    exp_id = exp.id
    with verification_path():
        exp.after_metrics = {"visibility": 50.0}
    await session.commit()
    with verification_path():
        exp.after_metrics = {"visibility": 80.0}
    with pytest.raises(ImmutableExperimentError):
        await session.commit()
    await session.rollback()
    exp = await session.get(Experiment, exp_id)
    await session.delete(exp)
    with pytest.raises(ImmutableExperimentError):
        await session.commit()
    await session.rollback()


async def test_proposed_experiment_may_still_be_edited_until_activation(session, org):
    from tests.reliability.helpers import proposed_intervention

    _, _, exp = await proposed_intervention(session, org)
    exp.before_metrics = {"visibility": 40.0}
    await session.commit()


async def test_experiment_history_survives_incident_delete_attempt(session, org):
    inc, _, exp = await executed_experiment(session, org)
    inc_id, exp_id = inc.id, exp.id
    with pytest.raises(IntegrityError):
        await session.execute(Incident.__table__.delete().where(Incident.id == inc_id))
    await session.rollback()
    assert await session.get(Experiment, exp_id) is not None


async def test_audit_events_are_append_only(session):
    ev = AuditEvent(actor_type="system", actor="t", entity_type="x", entity_id="1", event="e")
    session.add(ev)
    await session.commit()
    ev.event = "tampered"
    with pytest.raises(ImmutableAuditError):
        await session.commit()
    await session.rollback()


# ------------------------------------------------------------------ (6) exactly once under concurrency

async def _ready_for_reward(session, org):
    now = datetime.now(UTC)
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    await add_obs(session, exp, now - timedelta(hours=1), {"visibility": 55.0})
    return exp.id


async def test_two_workers_reward_exactly_once(sessionmaker, session, org):
    from app.learning.ingest import AlreadyRewarded, ingest_reward

    exp_id = await _ready_for_reward(session, org)

    async def worker():
        async with sessionmaker() as s:
            try:
                await ingest_reward(s, exp_id)
                await s.commit()
                return "ok"
            except AlreadyRewarded:
                await s.rollback()
                return "already"

    results = await asyncio.gather(worker(), worker(), worker())
    assert sorted(results) == ["already", "already", "ok"], results
    async with sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Reward).where(Reward.experiment_id == exp_id)) == 1
        assert await s.scalar(select(func.count()).select_from(PolicyVersion).where(
            PolicyVersion.source_experiment_id == exp_id)) == 1


async def test_db_rejects_second_reward_and_second_policy_version(session, org):
    from app.learning.ingest import ingest_reward

    exp_id = await _ready_for_reward(session, org)
    res = await ingest_reward(session, exp_id)
    parent_id = res.parent_version.id
    await session.commit()
    session.add(Reward(experiment_id=exp_id, components={}, total=0.0, weights={}))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    session.add(PolicyVersion(version="v9.9.9", algorithm="linucb", state={}, priors={}, n_updates=1,
                              parent_id=parent_id, source_experiment_id=exp_id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_simultaneous_approvals_one_wins(sessionmaker, session, org):
    from app.services import approvals as appr

    inc = await f.make_incident(session, org, state=S.AWAITING_APPROVAL.value)
    iv = await f.make_intervention(session, inc, selected=True)
    iv_id = iv.id

    async def worker(name: str, status: ApprovalStatus):
        async with sessionmaker() as s:
            try:
                iv_ = await s.get(type(iv), iv_id)
                ap = await appr.request_approval(s, iv_)
                await appr.decide_approval(s, ap, status, name)
                await s.commit()
                return status.value
            except appr.ApprovalError:
                await s.rollback()
                return "refused"

    results = await asyncio.gather(worker("a", ApprovalStatus.APPROVED), worker("b", ApprovalStatus.REJECTED))
    async with sessionmaker() as s:
        rows = (await s.execute(select(Approval).where(Approval.intervention_id == iv_id)
                                .order_by(Approval.created_at))).scalars().all()
    statuses = [r.status.value for r in rows]
    # Serialized on the intervention row: either the loser is refused (one decision), or a REJECT committed first and
    # the later APPROVE is a legitimate re-request. An approve followed by a reject is never possible.
    assert statuses in (["approved"], ["rejected"], ["rejected", "approved"]), (statuses, results)
    if len(statuses) == 1:
        assert results.count("refused") == 1


async def test_only_one_pending_approval_per_intervention(session, org):
    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    await f.make_approval(session, iv, status="pending")
    with pytest.raises(IntegrityError):
        await f.make_approval(session, iv, status="pending")
    await session.rollback()


async def test_two_verify_jobs_record_one_observation_and_one_reward(sessionmaker, session, org, no_queue, monkeypatch):
    from app.services import pipeline

    monkeypatch.delenv("PROFOUND_API_KEY", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    now = datetime.now(UTC)
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))
    exp_id = exp.id
    await f.make_signal(session, org, value=55.0, observed_at=now - timedelta(hours=1))

    async def job():
        async with sessionmaker() as s:
            try:
                return (await pipeline.verify(s, exp_id))["status"]
            except Exception as exc:  # noqa: BLE001
                await s.rollback()
                return type(exc).__name__

    results = await asyncio.gather(job(), job())
    get_settings.cache_clear()
    async with sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Reward)) == 1, results
        assert await s.scalar(select(func.count()).select_from(PolicyVersion).where(
            PolicyVersion.source_experiment_id == exp_id)) == 1
        assert await s.scalar(select(func.count()).select_from(Observation)) == 1


async def test_two_workers_investigate_same_incident_single_flight(sessionmaker, session, org, monkeypatch):
    from app.services import pipeline

    inc = await f.make_incident(session, org, state=S.DETECTED.value)
    inc_id = inc.id
    entered = []

    async def slow_body(s, iid):
        entered.append(iid)
        await asyncio.sleep(0.3)
        return {"incident_id": str(iid), "status": "done"}

    monkeypatch.setattr(pipeline, "_investigate", slow_body)

    async def worker():
        async with sessionmaker() as s:
            return (await pipeline.investigate(s, inc_id))["status"]

    results = await asyncio.gather(worker(), worker())
    assert sorted(results) == ["already_running", "done"] and len(entered) == 1


# ------------------------------------------------------------------ (8)(9) audit + atomicity

async def test_ledger_audit_events_for_consequential_mutations(session, org):
    inc = await f.make_incident(session, org)
    ev = await f.make_evidence(session, inc)
    h = await f.make_hypothesis(session, inc, [ev.id])
    h.status = HypothesisStatus.CONFIRMED.value
    await session.commit()
    _, _, exp = await executed_experiment(session, org)
    await add_obs(session, exp, datetime.now(UTC))
    names = {e for (e,) in (await session.execute(select(AuditEvent.event))).all()}
    assert {"incident.created", "evidence.created", "hypothesis.proposed", "hypothesis.confirmed",
            "observation.recorded"} <= names


async def test_ledger_audit_activation_and_verification(session, org):
    from app.experiments import ledger
    from app.experiments.status import transition

    from tests.reliability.helpers import proposed_intervention

    _, iv, exp = await proposed_intervention(session, org, action="observe", change={"kind": "observe"})
    ap = None
    await ledger.attach_approval(session, exp, ap)
    exp.executed_at = datetime.now(UTC) - timedelta(days=3)
    transition(exp, ExperimentStatus.EXECUTING)
    transition(exp, ExperimentStatus.EXECUTED)
    transition(exp, ExperimentStatus.AWAITING_VERIFICATION)
    from app.experiments.guards import verification_path

    with verification_path():
        exp.after_metrics = {"visibility": 50.0}
    transition(exp, ExperimentStatus.VERIFIED)
    await session.commit()
    names = {e for (e,) in (await session.execute(select(AuditEvent.event))).all()}
    assert {"experiment.activated", "experiment.verified"} <= names


async def test_approval_activation_is_one_transaction_and_rolls_back_on_failure(app_client, session, org, monkeypatch):
    from tests.reliability.helpers import proposed_intervention

    inc, iv, exp = await proposed_intervention(session, org, action="observe", change={"kind": "observe"})
    iv_id, exp_id, inc_id = iv.id, exp.id, inc.id
    import app.api.routes.interventions as route

    async def boom(*a, **kw):
        raise RuntimeError("injected failure after decision, ledger attach and before commit")

    monkeypatch.setattr(route, "audit", boom)
    with pytest.raises(RuntimeError):
        await app_client.post(f"/api/interventions/{iv_id}/approve", headers={"X-Actor": "pat@example.com"})
    session.expire_all()
    assert (await session.scalar(select(func.count()).select_from(Approval))) == 0
    e = await session.get(Experiment, exp_id)
    assert e.status == ExperimentStatus.PROPOSED and e.approval_id is None and e.approver is None
    assert (await session.get(Incident, inc_id)).state == S.AWAITING_APPROVAL.value
    names = {n for (n,) in (await session.execute(select(AuditEvent.event))).all()}
    assert not names & {"approval.approved", "experiment.activated", "intervention.approved"}


# ------------------------------------------------------------------ (10) audit-db

async def test_audit_db_clean_then_detects_planted_violations(sessionmaker, session, org, monkeypatch):
    from app.devtools import audit_db

    inc = await f.make_incident(session, org)
    ev = await f.make_evidence(session, inc)
    assert (await audit_db.run())["clean"] is True

    h = await f.make_hypothesis(session, inc, [str(uuid.uuid4())])
    # bypass the ORM guards with SQL to plant corruption (audit must report it, never repair it)
    await session.execute(update(Hypothesis).where(Hypothesis.id == h.id).values(status="confirmed"))
    _, _, exp = await executed_experiment(session, org)
    await add_obs(session, exp, datetime.now(UTC) - timedelta(days=30))  # before execution
    await session.execute(update(Experiment).where(Experiment.id == exp.id).values(before_metrics={}))
    await session.commit()
    report = await audit_db.run()
    v = report["violations"]
    assert not report["clean"]
    assert str(h.id) in v["confirmed_hypothesis_without_valid_evidence"]
    assert str(exp.id) in v["experiment_without_baseline"]
    assert v["observation_not_after_execution"]
    assert str(ev.id) not in str(v.get("orphan_evidence", ""))
