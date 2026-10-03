"""M8 (V3 audit): approve / reject / modify through the HTTP API with X-Actor, activation WITHOUT GitHub.

Every test runs with GITHUB_* unset (autouse fixture asserts it). Approval of a mutating action activates the experiment
and issues a ManualExecutor package; only a human recording the execution starts the verification window.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import get_settings
from app.domain.enums import ApprovalStatus, ExperimentStatus, IncidentState
from app.models.interventions import Approval, Execution, Experiment, Reward
from app.models.policy import PolicyVersion
from sqlalchemy import func, select

from tests import factories as f
from tests.reliability.helpers import CHANGE, MODIFIED, OBSERVE, proposed_intervention

H = {"X-Actor": "alice@testco.example"}


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    for k in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_BASE_BRANCH", "GITHUB_CONTENT_ROOT"):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert not s.github_token, "GITHUB_TOKEN must be unset for the M8 tests (GitHub is never required)"
    yield
    get_settings.cache_clear()


async def _exp(session, iv_id) -> Experiment:
    return (await session.execute(select(Experiment).where(Experiment.intervention_id == iv_id)
                                  .execution_options(populate_existing=True))).scalars().one()


async def test_approve_activates_experiment_and_issues_manual_package_without_github(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"note": "ship it"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["incident_state"] == IncidentState.APPROVED.value and body["message"] is None
    assert body["intervention"]["executor"] == "manual" and body["intervention"]["manual_execution_pending"] is True
    assert body["intervention"]["package"]["changes"][0]["path"] == "content/security.md"
    e = await _exp(session, iv.id)
    # activated = approved + linked to the human approval; NOT executed, window NOT started, nothing measured
    assert e.status == ExperimentStatus.APPROVED and e.approver == H["X-Actor"] and e.approval_id is not None
    # (the window shown at proposal time is only a plan: with executed_at unset nothing can qualify as an outcome)
    assert e.executed_at is None and e.after_metrics is None
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.executor == "manual" and ex.status == "awaiting_human_execution" and ex.dry_run is False
    assert ex.package and ex.executed_by is None, "package issued, nobody has executed anything yet"
    # no reward, no policy update merely because it was approved
    assert (await session.execute(select(Reward))).scalars().all() == []
    assert (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar() == 1


async def test_approval_snapshots_exact_original_action_and_experiment_ledger(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    ap = (await session.execute(select(Approval).execution_options(populate_existing=True))).scalars().one()
    assert ap.status == ApprovalStatus.APPROVED and ap.decided_actor_type == "human" and ap.decided_by == H["X-Actor"]
    assert ap.requested_change == CHANGE and ap.modified_change is None
    e = await _exp(session, iv.id)
    assert e.proposed_change == CHANGE and e.approved_change == CHANGE
    assert e.before_metrics == {"visibility": 37.0} and e.policy_version_id is not None


async def test_modify_persists_original_and_modified_verbatim_and_executes_the_modified_one(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/modify", json={"modified_change": MODIFIED, "note": "tweak"},
                              headers=H)
    assert r.status_code == 200, r.text
    ap = (await session.execute(select(Approval).execution_options(populate_existing=True))).scalars().one()
    assert ap.status == ApprovalStatus.MODIFIED
    assert ap.requested_change == CHANGE, "the original proposal is never rewritten"
    assert ap.modified_change == MODIFIED, "the human's edit is stored verbatim"
    e = await _exp(session, iv.id)
    assert e.proposed_change == CHANGE and e.approved_change == MODIFIED
    assert e.status == ExperimentStatus.APPROVED
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.status == "awaiting_human_execution"
    assert "OIDC" in str(ex.package), "the package must carry the HUMAN-modified change"


async def test_reject_blocks_activation_and_no_package_is_issued(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/reject", json={"note": "no"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["incident_state"] == IncidentState.INTERVENTION_PROPOSED.value
    ap = (await session.execute(select(Approval).execution_options(populate_existing=True))).scalars().one()
    assert ap.status == ApprovalStatus.REJECTED
    e = await _exp(session, iv.id)
    assert e.status in (ExperimentStatus.REJECTED, ExperimentStatus.PROPOSED) and e.executed_at is None
    assert (await session.execute(select(Execution))).scalars().all() == []
    assert (await app_client.post(f"/api/interventions/{iv.id}/execute", headers=H)).status_code == 409
    assert (await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers=H)).status_code == 409


@pytest.mark.parametrize("actor", ["system", "model", "llm", "pipeline", "worker", "agent", "bot", "scheduler", "SYSTEM"])
@pytest.mark.parametrize("verb", ["approve", "reject", "modify"])
async def test_non_human_actor_cannot_decide_via_api(app_client, session, org, actor, verb):
    """REPAIRED by V3: the API used to hard-code actor_type='human', so X-Actor: system could approve."""
    inc, iv, exp = await proposed_intervention(session, org)
    body = {"modified_change": MODIFIED} if verb == "modify" else None
    r = await app_client.post(f"/api/interventions/{iv.id}/{verb}", json=body, headers={"X-Actor": actor})
    assert r.status_code == 403, r.text
    await session.rollback()
    assert (await session.execute(select(Approval).where(Approval.status != ApprovalStatus.PENDING))).scalars().all() == []
    assert (await session.execute(select(Execution))).scalars().all() == []
    e = await _exp(session, iv.id)
    assert e.status == ExperimentStatus.PROPOSED and e.approval_id is None


async def test_decided_approval_is_immutable_via_api(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    assert (await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)).status_code == 200
    for verb, body in (("reject", None), ("modify", {"modified_change": MODIFIED})):
        r = await app_client.post(f"/api/interventions/{iv.id}/{verb}", json=body, headers={"X-Actor": "bob"})
        assert r.status_code == 409, (verb, r.text)
    repeat = await app_client.post(f"/api/interventions/{iv.id}/approve", headers={"X-Actor": "bob"})
    assert repeat.status_code == 200, "repeating the SAME decision is retry-safe and changes nothing"
    ap = (await session.execute(select(Approval).execution_options(populate_existing=True))).scalars().one()
    assert ap.status == ApprovalStatus.APPROVED and ap.decided_by == H["X-Actor"] and ap.modified_change is None
    assert len((await session.execute(select(Execution))).scalars().all()) == 1, "no second package"


async def test_rejected_then_approve_is_blocked_decision_stays_rejected(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    assert (await app_client.post(f"/api/interventions/{iv.id}/reject", headers=H)).status_code == 200
    again = await app_client.post(f"/api/interventions/{iv.id}/approve", headers={"X-Actor": "bob"})
    # either refused outright, or a brand-new pending request was needed first: never a silent flip of the old decision
    old = (await session.execute(select(Approval).where(Approval.status == ApprovalStatus.REJECTED)
                                 .execution_options(populate_existing=True))).scalars().all()
    assert len(old) == 1 and old[0].decided_by == H["X-Actor"], again.text


async def test_human_records_execution_starts_window_and_never_before(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    applied = datetime.now(UTC) - timedelta(hours=1)
    r = await app_client.post(f"/api/interventions/{iv.id}/executed", headers=H,
                              json={"executed_at": applied.isoformat(), "reference_url": "https://testco.example/x"})
    assert r.status_code == 200, r.text
    e = await _exp(session, iv.id)
    assert e.status == ExperimentStatus.AWAITING_VERIFICATION and e.dry_run is False
    assert abs(((e.executed_at if e.executed_at.tzinfo else e.executed_at.replace(tzinfo=UTC)) - applied)
               .total_seconds()) < 1
    start = e.verification_window_start if e.verification_window_start.tzinfo else \
        e.verification_window_start.replace(tzinfo=UTC)
    assert start >= applied + timedelta(hours=get_settings().verification_delay_hours) - timedelta(seconds=1)
    assert e.after_metrics is None and (await session.execute(select(Reward))).scalars().all() == []


async def test_non_human_actor_cannot_record_execution(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    for actor in ("system", "pipeline", "model", "worker"):
        r = await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers={"X-Actor": actor})
        assert r.status_code == 403, (actor, r.text)
    e = await _exp(session, iv.id)
    assert e.status == ExperimentStatus.APPROVED and e.executed_at is None


async def test_record_execution_before_approval_is_refused(app_client, session, org):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers=H)
    assert r.status_code == 409
    e = await _exp(session, iv.id)
    assert e.status == ExperimentStatus.PROPOSED and e.executed_at is None


async def test_observe_path_approve_starts_observing_without_a_human_execution_step(app_client, session, org):
    """`observe` makes no external change: no GitHub, no manual package; activation starts the measurement window."""
    inc, iv, exp = await proposed_intervention(session, org, action="observe", change=OBSERVE)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert r.status_code == 200, r.text
    e = await _exp(session, iv.id)
    assert e.status == ExperimentStatus.AWAITING_VERIFICATION and e.dry_run is False
    assert e.verification_window_start is not None and e.after_metrics is None
    assert (await session.execute(select(Reward))).scalars().all() == []
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.executor == "manual" and ex.status == "succeeded"


async def test_unknown_intervention_and_github_not_configured(app_client, session, org):
    assert (await app_client.post(f"/api/interventions/{uuid.uuid4()}/approve", headers=H)).status_code == 404
    inc, iv, _ = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"executor": "github"}, headers=H)
    assert r.status_code == 409 and r.json()["error"]["details"]["code"] == "github_not_configured"
    assert (await session.execute(select(Approval).where(Approval.status != ApprovalStatus.PENDING))).scalars().all() == []


async def test_capabilities_do_not_degrade_without_github(app_client):
    body = (await app_client.get("/api/system/capabilities")).json()
    assert "github" not in body["capabilities"], "GitHub is optional: it must not be a core capability"
    assert body["executors"]["manual"]["state"] == "healthy"
    assert body["executors"]["github"]["state"] != "healthy"


async def _noop():  # keep factories import used for static checkers
    return f.NOW
