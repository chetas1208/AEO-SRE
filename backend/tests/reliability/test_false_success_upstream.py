"""Adversarial hunt, upstream half: the system must not claim a root cause or an intervention it cannot justify.

Expected conservative outcomes: unconfirmed / observe-only / discarded / unavailable - never fabricated success.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.domain.enums import ActionType, ApprovalStatus, ExperimentStatus, IncidentState
from app.incidents.state_machine import IllegalTransition, transition
from app.investigation.evidence_gate import confirm_aeo_root_cause
from app.models.interventions import Execution, Experiment, Intervention
from app.models.policy import PolicyDecision
from sqlalchemy import select

from tests import factories as f
from tests.reliability.helpers import ctx_json, executed_experiment  # noqa: F401
from tests.unit.test_evidence_gate import Hyp, external, full_set, owned, profound


def test_unconfirmed_hypothesis_is_not_confirmed_without_full_evidence_set():
    only_profound = [profound()]
    r = confirm_aeo_root_cause(Hyp(evidence_ids=["p1"]), only_profound)
    assert r.confirmed is False and r.missing, "a single Profound signal can never confirm a root cause"
    assert confirm_aeo_root_cause(Hyp(), []).confirmed is False
    assert confirm_aeo_root_cause(Hyp(), [owned(), external()]).confirmed is False, "no Profound metric change"


def test_missing_evidence_means_unconfirmed_with_reasons():
    r = confirm_aeo_root_cause(Hyp(evidence_ids=[]), full_set())
    assert not r.confirmed and r.reasons, "uncited hypotheses are not confirmed even if evidence exists"
    r = confirm_aeo_root_cause(Hyp(evidence_ids=["nope"]), None)  # type: ignore[arg-type]
    assert not r.confirmed


def test_hypothesis_citing_nonexistent_evidence_ids_is_not_confirmed():
    evs = full_set()
    ids = [e.id for e in evs]
    r = confirm_aeo_root_cause(Hyp(evidence_ids=[*ids, "ghost-1", "ghost-2"]), evs)
    assert not r.confirmed and "ghost-1" in " ".join(r.reasons)


def test_llm_output_citing_nonexistent_evidence_is_discarded_and_rules_survive():
    from app.investigation.rca import run_rca

    from tests.unit.test_rca import CITATION_LOST, FakeLLM, good, inc

    payload = json.dumps({"hypotheses": [good(evidence_ids=["p2", "invented"]),
                                         good(title="Pure invention", evidence_ids=["does-not-exist"])]})
    res = run_rca(inc(), [CITATION_LOST], FakeLLM(payload))
    llm_h = [h for h in res.hypotheses if h.produced_by.startswith("llm:")]
    assert llm_h == [], "an LLM hypothesis may only cite evidence that exists"
    for h in res.hypotheses:
        assert "invented" not in h.evidence_ids and "does-not-exist" not in h.evidence_ids
        assert h.status.value == "proposed", "no hypothesis is ever born confirmed"


def test_model_provider_unavailable_degrades_to_rules_only_never_invents():
    from app.investigation.rca import run_rca

    from tests.unit.test_rca import CITATION_LOST, FakeLLM, inc

    res = run_rca(inc(), [CITATION_LOST], FakeLLM(RuntimeError("provider down")))
    assert not res.llm_used and res.hypotheses
    assert not any(h.produced_by.startswith("llm:") for h in res.hypotheses)
    assert any("llm unavailable" in w for w in res.warnings)


def test_state_machine_refuses_confirmation_without_a_passing_gate():
    inc = SimpleNamespace(id=None, state=IncidentState.ROOT_CAUSE_PROPOSED)
    with pytest.raises(IllegalTransition):
        transition(inc, IncidentState.ROOT_CAUSE_CONFIRMED, "claude", "I am sure")
    with pytest.raises(IllegalTransition):
        transition(inc, IncidentState.ROOT_CAUSE_CONFIRMED, "claude", "gate said no",
                   gate=SimpleNamespace(confirmed=False, reasons=["x"], missing=["y"]))
    assert inc.state == IncidentState.ROOT_CAUSE_PROPOSED


async def test_unconfirmed_incident_can_only_be_proposed_observe_with_no_propensity_claim(
        session, org, no_queue, monkeypatch):
    """pipeline.propose on a ROOT_CAUSE_PROPOSED (gate did not confirm) incident: the only selectable action is
    observe, recorded as a rule fallback (excluded from off-policy learning), never a mutating change."""
    from app.services import pipeline

    inc = await f.make_incident(session, org, state=IncidentState.ROOT_CAUSE_PROPOSED)
    await f.make_hypothesis(session, inc, status="proposed")
    await session.refresh(inc)
    iid = inc.id
    out = await pipeline.propose(session, iid)
    assert out["action"] == ActionType.OBSERVE.value, out
    pd = (await session.execute(select(PolicyDecision).where(PolicyDecision.incident_id == iid))).scalars().first()
    assert pd.selected_action == "observe" and pd.selection_basis == "rule_fallback"
    assert pd.meta.get("constrained_to_observe") is True
    sel = (await session.execute(select(Intervention).where(Intervention.incident_id == iid,
                                                            Intervention.selected.is_(True)))).scalars().one()
    assert ActionType(sel.action) == ActionType.OBSERVE


async def test_manual_package_issued_but_never_recorded_stays_pending_and_cannot_be_measured(
        session, org, no_queue):
    """Manual executor not activated by a human: approval + package exist, but there is no execution, no window, no
    verification and no reward - however long we wait and however many observations exist."""
    from app.services import pipeline
    from app.services.pipeline import PermanentError

    from tests.reliability.helpers import approve_and_package

    inc, iv, exp = await approve_and_package(session, org)
    iid, exid = inc.id, exp.id
    await add_obs_safe(session, exid)
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.status == "awaiting_human_execution" and ex.executed_by is None
    with pytest.raises(PermanentError):
        await pipeline.verify(session, exid, force=True)
    await session.rollback()
    from app.learning.ingest import NotRewardable, ingest_reward

    with pytest.raises(NotRewardable):
        await ingest_reward(session, exid)
    await session.rollback()
    e = await session.get(Experiment, exid, populate_existing=True)
    assert e.status == ExperimentStatus.APPROVED and e.executed_at is None and e.after_metrics is None
    from app.models.core import Incident

    assert (await session.get(Incident, iid, populate_existing=True)).state == IncidentState.APPROVED.value


async def add_obs_safe(session, exp_id):
    """A stray observation row (e.g. from a buggy collector) attached to a never-executed experiment."""
    from datetime import timedelta

    from app.models.interventions import Observation

    session.add(Observation(experiment_id=exp_id, metrics={"visibility": 99.0}, observed_at=f.NOW + timedelta(days=9),
                            source="profound"))
    await session.commit()


async def test_rejected_intervention_never_executes_or_measures(app_client, session, org):
    from tests.reliability.helpers import proposed_intervention

    inc, iv, exp = await proposed_intervention(session, org)
    ivid = iv.id
    assert (await app_client.post(f"/api/interventions/{ivid}/reject", headers={"X-Actor": "alice"})).status_code == 200
    assert (await app_client.post(f"/api/interventions/{ivid}/executed", json={}, headers={"X-Actor": "alice"})
            ).status_code == 409
    assert (await session.execute(select(Execution))).scalars().all() == []
    from app.models.interventions import Approval

    ap = (await session.execute(select(Approval).execution_options(populate_existing=True))).scalars().one()
    assert ap.status == ApprovalStatus.REJECTED
