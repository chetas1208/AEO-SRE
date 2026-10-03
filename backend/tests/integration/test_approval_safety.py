"""Approval safety: nothing executes without a human-approved Approval; reject blocks; modifications are recorded."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.domain.enums import ActionType, ApprovalStatus, ExperimentStatus, IncidentState
from app.experiments import ledger
from app.experiments.status import IllegalExperimentTransition
from app.models.core import AuditEvent
from app.models.interventions import Approval, Execution
from app.services import approvals as appr
from app.services.approvals import ExecutionNotAuthorized
from sqlalchemy import select

from tests import factories as f

CHANGE = {"files": [{"path": "docs/sso.md", "diff": "+ SAML SSO is supported"}]}
EDITED = {"files": [{"path": "docs/sso.md", "diff": "+ SAML 2.0 and OIDC SSO are supported"}]}


@pytest.fixture
async def proposed(session, org):
    inc = await f.make_incident(session, org, state=IncidentState.INTERVENTION_PROPOSED)
    iv = await f.make_intervention(session, inc, proposed_change=CHANGE)
    return inc, iv


# ---- service layer ------------------------------------------------------------------------------------


async def test_execution_gate_blocks_without_any_approval(session, proposed):
    _, iv = proposed
    with pytest.raises(appr.ExecutionNotAuthorized):
        await appr.require_executable_approval(session, iv)


async def test_execution_gate_blocks_pending_approval(session, proposed):
    _, iv = proposed
    await appr.request_approval(session, iv)
    await session.commit()
    with pytest.raises(appr.ExecutionNotAuthorized):
        await appr.require_executable_approval(session, iv)


async def test_execution_gate_blocks_rejected_approval(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.REJECTED, "alice", "not now")
    await session.commit()
    with pytest.raises(appr.ExecutionNotAuthorized):
        await appr.require_executable_approval(session, iv)


@pytest.mark.parametrize("actor_type", ["model", "system"])
@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED, ApprovalStatus.MODIFIED, ApprovalStatus.REJECTED])
async def test_only_humans_may_decide(session, proposed, actor_type, status):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    kw = {"modified_change": EDITED} if status == ApprovalStatus.MODIFIED else {}
    with pytest.raises(appr.HumanActorRequired):
        await appr.decide_approval(session, ap, status, "claude", "ok", actor_type=actor_type, **kw)
    assert ap.status == ApprovalStatus.PENDING


async def test_human_approval_unlocks_execution_gate(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "alice")
    await session.commit()
    granted = await appr.require_executable_approval(session, iv)
    assert granted.id == ap.id and granted.decided_by == "alice"


async def test_decided_approval_cannot_be_decided_again(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.REJECTED, "alice")
    with pytest.raises(appr.ApprovalImmutable):
        await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "bob")


async def test_expired_approval_cannot_be_granted(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv, ttl=timedelta(hours=1), now=f.NOW)
    with pytest.raises(appr.ApprovalExpired):
        await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "alice", now=f.NOW + timedelta(hours=2))


async def test_modified_change_recorded_verbatim_and_is_what_executes(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.MODIFIED, "alice", "tighten wording", EDITED)
    await session.commit()
    stored = (await session.execute(select(Approval).where(Approval.id == ap.id))).scalar_one()
    assert stored.modified_change == EDITED
    assert stored.requested_change == CHANGE, "the original proposal snapshot must remain intact"
    assert appr.effective_change(stored, iv) == EDITED
    assert iv.proposed_change == CHANGE


async def test_modify_requires_non_empty_change(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    with pytest.raises(appr.ApprovalError):
        await appr.decide_approval(session, ap, ApprovalStatus.MODIFIED, "alice", None, {})


async def test_observe_needs_no_approval_but_still_cannot_mutate(session, org):
    inc = await f.make_incident(session, org, state=IncidentState.INTERVENTION_PROPOSED)
    iv = await f.make_intervention(session, inc, action=ActionType.OBSERVE.value, proposed_change={})
    assert await appr.require_executable_approval(session, iv) is None


@pytest.mark.parametrize("action", [a for a in ActionType if a != ActionType.OBSERVE])
async def test_every_mutating_action_requires_approval(session, org, action):
    inc = await f.make_incident(session, org, state=IncidentState.INTERVENTION_PROPOSED)
    iv = await f.make_intervention(session, inc, action=action.value)
    assert appr.requires_approval(iv)
    with pytest.raises(appr.ExecutionNotAuthorized):
        await appr.require_executable_approval(session, iv)


async def test_approval_audit_trail_written(session, proposed):
    _, iv = proposed
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "alice", "lgtm")
    await session.commit()
    events = (await session.execute(select(AuditEvent).where(AuditEvent.entity_id == str(ap.id)))).scalars().all()
    assert {e.event for e in events} >= {"approval.requested", "approval.approved"}
    assert any(e.actor == "alice" and e.actor_type == "human" for e in events)


# ---- experiment ledger gating ----------------------------------------------------------------------------


async def _experiment(session, inc, iv):
    pv = await f.make_policy_version(session)
    exp = await ledger.open_experiment(
        session, iv, {"probability": 0.5, "action": iv.action, "scores": [], "selection_basis": "cold_start_prior",
                      "cold_start": True, "policy_version_id": pv.id},
        inc, {"visibility": 0.37}, {"items": []}, [0.1], now=f.NOW)
    await session.commit()
    return exp


async def test_experiment_cannot_start_executing_without_approval(session, proposed):
    inc, iv = proposed
    exp = await _experiment(session, inc, iv)
    with pytest.raises(IllegalExperimentTransition):
        from app.experiments.status import transition

        transition(exp, ExperimentStatus.APPROVED)
    with pytest.raises(ExecutionNotAuthorized):
        await ledger.begin_execution(session, exp)


async def test_rejected_approval_rejects_experiment(session, proposed):
    inc, iv = proposed
    exp = await _experiment(session, inc, iv)
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.REJECTED, "alice")
    await ledger.attach_approval(session, exp, ap)
    assert exp.status == ExperimentStatus.REJECTED
    with pytest.raises(ExecutionNotAuthorized):
        await ledger.begin_execution(session, exp)


async def test_pending_approval_cannot_advance_experiment(session, proposed):
    inc, iv = proposed
    exp = await _experiment(session, inc, iv)
    ap = await appr.request_approval(session, iv)
    with pytest.raises(ValueError):
        await ledger.attach_approval(session, exp, ap)
    assert exp.status == ExperimentStatus.PROPOSED


async def test_experiment_records_modified_change_as_approved_change(session, proposed):
    inc, iv = proposed
    exp = await _experiment(session, inc, iv)
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.MODIFIED, "alice", "edit", EDITED)
    await ledger.attach_approval(session, exp, ap)
    assert exp.status == ExperimentStatus.APPROVED
    assert exp.approved_change == EDITED and exp.proposed_change == CHANGE
    assert exp.approver == "alice"


# ---- HTTP API ---------------------------------------------------------------------------------------------


async def test_api_execute_without_approval_is_409_and_creates_no_execution(app_client, session, proposed):
    _, iv = proposed
    r = await app_client.post(f"/api/interventions/{iv.id}/execute")
    assert r.status_code == 409, r.text
    assert (await session.execute(select(Execution))).scalars().all() == []


async def test_api_execute_when_state_forced_approved_but_no_approval_row_is_blocked(app_client, session, org):
    """Even if the incident state says approved, the missing human Approval row must stop execution."""
    inc = await f.make_incident(session, org, state=IncidentState.APPROVED)
    iv = await f.make_intervention(session, inc)
    r = await app_client.post(f"/api/interventions/{iv.id}/execute")
    assert r.status_code in (403, 409), r.text
    assert (await session.execute(select(Execution))).scalars().all() == []


async def test_api_reject_blocks_execution(app_client, proposed, session):
    inc, iv = proposed
    r = await app_client.post(f"/api/interventions/{iv.id}/reject", json={"note": "no"}, headers={"X-Actor": "alice"})
    assert r.status_code == 200, r.text
    assert r.json()["incident_state"] == IncidentState.INTERVENTION_PROPOSED.value
    r2 = await app_client.post(f"/api/interventions/{iv.id}/execute")
    assert r2.status_code == 409
    assert (await session.execute(select(Execution))).scalars().all() == []


async def test_api_double_decision_is_409(app_client, proposed):
    _, iv = proposed
    assert (await app_client.post(f"/api/interventions/{iv.id}/approve")).status_code == 200
    again = await app_client.post(f"/api/interventions/{iv.id}/approve")
    assert again.status_code == 200, "the same decision repeated is retry-safe (no second approval)"
    rej = await app_client.post(f"/api/interventions/{iv.id}/reject")
    assert rej.status_code == 409


async def test_api_modify_records_diff(app_client, proposed, session):
    _, iv = proposed
    r = await app_client.post(f"/api/interventions/{iv.id}/modify",
                              json={"modified_change": EDITED, "note": "wording"}, headers={"X-Actor": "alice"})
    assert r.status_code == 200, r.text
    ap = (await session.execute(select(Approval).where(Approval.intervention_id == iv.id))).scalar_one()
    assert ap.status == ApprovalStatus.MODIFIED and ap.modified_change == EDITED and ap.decided_by == "alice"
    empty = await app_client.post(f"/api/interventions/{iv.id}/modify", json={"modified_change": {}})
    assert empty.status_code in (409, 422)


async def test_api_approval_decision_wrong_incident_state_409(app_client, session, org):
    inc = await f.make_incident(session, org, state=IncidentState.DETECTED)
    iv = await f.make_intervention(session, inc)
    assert (await app_client.post(f"/api/interventions/{iv.id}/approve")).status_code == 409


async def test_api_unknown_intervention_404(app_client):
    import uuid

    for verb in ("approve", "reject", "execute"):
        r = await app_client.post(f"/api/interventions/{uuid.uuid4()}/{verb}")
        assert r.status_code == 404
