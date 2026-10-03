"""Manual execution: the default path. A human applies the packaged change and records it; the system then measures.

No GITHUB_* configuration exists anywhere in these tests. A manual execution is a REAL execution (dry_run=False):
it verifies, rewards and counts toward historical outcome ranges.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import get_settings
from app.domain.enums import ApprovalStatus, ExperimentStatus, IncidentState
from app.experiments import ledger
from app.interventions.changes import FileChange, ManualTask, ProposedChange, wrap_section
from app.interventions.manual import (
    AlreadyExecuted,
    InvalidExecutionTime,
    NotExecutable,
    prepare_manual_package,
    record_manual_execution,
)
from app.models.interventions import Execution, Experiment, Observation, Reward
from app.models.policy import PolicyVersion
from app.services import approvals as appr
from app.services import pipeline
from sqlalchemy import select

from tests import factories as f


def _ctx():
    from app.policy import encode_context
    from app.policy.features import FEATURE_NAMES

    return {"names": list(FEATURE_NAMES), "values": [float(v) for v in encode_context(visibility_delta=-0.4)]}


CHANGE = ProposedChange(
    title="Add SAML section", target_url="https://testco.example/enterprise/security", summary="add section",
    files=[FileChange(path="content/security.md", old_content="# Security\n",
                      new_content=wrap_section("incident-1", "## SAML SSO\n\nSAML SSO is supported."),
                      patch_mode="upsert_section", section_id="incident-1")]).to_json()
OBSERVE = ProposedChange(kind="observe", title="Monitor", summary="watch",
                         manual_task=ManualTask(kind="observe", title="Monitor", checklist=["re-measure"])).to_json()


async def _setup(session, org, *, action="update_existing_page", change=CHANGE, approve=True, package=True,
                 state=IncidentState.APPROVED):
    inc = await f.make_incident(session, org, state=state)
    iv = await f.make_intervention(session, inc, action=action, proposed_change=change, selected=True)
    pv = await f.make_policy_version(session)
    exp = await ledger.open_experiment(
        session, iv, {"probability": 0.6, "action": iv.action, "scores": [], "selection_basis": "cold_start_prior",
                      "cold_start": True, "policy_version_id": pv.id},
        inc, {"visibility": 0.37}, {}, _ctx(), now=f.NOW)
    if approve:
        ap = await appr.request_approval(session, iv)
        await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "alice", "ok")
        await ledger.attach_approval(session, exp, ap)
    await session.commit()
    if approve and package:
        await prepare_manual_package(session, iv.id)
        await session.commit()
    return inc, iv, exp


async def test_record_marks_real_execution_and_advances_experiment_and_incident(session, org):
    inc, iv, exp = await _setup(session, org)
    executed_at = datetime.now(UTC) - timedelta(hours=3)
    out = await record_manual_execution(session, iv.id, "alice", executed_at, "https://testco.example/sso",
                                        "published")
    await session.commit()
    ex = await session.get(Execution, out.execution.id, populate_existing=True)
    assert ex.status == "succeeded" and ex.dry_run is False and ex.executor == "manual"
    assert ex.reference == "https://testco.example/sso" and ex.note == "published" and ex.deviation is False
    assert ex.executed_by == "alice" and ex.actual_change is None and ex.package
    exp = await session.get(Experiment, exp.id, populate_existing=True)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.dry_run is False
    assert exp.executed_at == executed_at and exp.executor == "manual"
    delay = timedelta(hours=get_settings().verification_delay_hours)  # 48h Profound lag respected
    assert exp.verification_window_start == executed_at + delay
    inc = await session.get(type(inc), inc.id, populate_existing=True)
    assert inc.state == IncidentState.AWAITING_VERIFICATION.value


async def test_deviation_is_recorded_verbatim_and_flagged(session, org):
    _, iv, exp = await _setup(session, org)
    text = "I used a shorter paragraph:\n  SAML SSO available on Enterprise.  "
    out = await record_manual_execution(session, iv.id, "alice", actual_change=text)
    await session.commit()
    ex = await session.get(Execution, out.execution.id, populate_existing=True)
    assert out.deviation and ex.deviation is True and ex.actual_change["text"] == text  # verbatim, whitespace kept
    exp = await session.get(Experiment, exp.id, populate_existing=True)
    assert any("deviation" in (t.get("reason") or "") for t in exp.timeline)
    assert exp.proposed_change == CHANGE  # the proposal itself is never rewritten


async def test_non_human_actor_cannot_record(session, org):
    _, iv, _ = await _setup(session, org)
    for kw in ({"executed_by": "alice", "actor_type": "model"}, {"executed_by": "pipeline"},
               {"executed_by": "  ", "actor_type": "human"}, {"executed_by": "system"}):
        with pytest.raises(appr.HumanActorRequired):
            await record_manual_execution(session, iv.id, **kw)
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.status == "awaiting_human_execution"  # nothing changed


async def test_future_executed_at_rejected(session, org):
    _, iv, exp = await _setup(session, org)
    with pytest.raises(InvalidExecutionTime):
        await record_manual_execution(session, iv.id, "alice", datetime.now(UTC) + timedelta(hours=1))
    await session.rollback()
    exp = await session.get(Experiment, exp.id, populate_existing=True)
    assert exp.status == ExperimentStatus.APPROVED and exp.executed_at is None


async def test_double_record_is_idempotent_conflict(session, org):
    _, iv, _ = await _setup(session, org)
    await record_manual_execution(session, iv.id, "alice")
    await session.commit()
    with pytest.raises(AlreadyExecuted):
        await record_manual_execution(session, iv.id, "alice")
    await session.rollback()
    assert len((await session.execute(select(Execution))).scalars().all()) == 1


async def test_requires_approved_incident_and_approval(session, org):
    _, iv, _ = await _setup(session, org, approve=False, state=IncidentState.AWAITING_APPROVAL)
    with pytest.raises(NotExecutable):
        await record_manual_execution(session, iv.id, "alice")  # incident not approved
    await session.rollback()
    await session.refresh(org)
    inc2, iv2, _ = await _setup(session, org, approve=False)  # state forced approved but no approval row
    with pytest.raises(NotExecutable):
        await record_manual_execution(session, iv2.id, "alice")
    await session.rollback()
    assert (await session.execute(select(Execution))).scalars().all() == []


async def test_package_issued_lazily_when_missing(session, org):
    _, iv, _ = await _setup(session, org, package=False)
    out = await record_manual_execution(session, iv.id, "alice")
    assert out.execution.package and out.execution.status == "succeeded"


async def test_observe_needs_no_human_step_and_starts_observing(session, org, emit):
    inc, iv, exp = await _setup(session, org, action="observe", change=OBSERVE, approve=False,
                                state=IncidentState.AWAITING_APPROVAL)
    out = await pipeline.execute(session, iv.id)
    await session.commit()
    assert out["status"] == "executed" and out["dry_run"] is False and out["executor"] == "manual", out
    exp = await session.get(Experiment, exp.id, populate_existing=True)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.verification_window_start is not None
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.status == "succeeded" and ex.dry_run is False and ex.executor == "manual"
    with pytest.raises(NotExecutable):  # observe has no human step to record
        await record_manual_execution(session, iv.id, "alice")


async def test_manual_execution_counts_toward_reward_policy_update_and_outcome_range(session, org):
    """A manual execution is NOT a dry run: after the window a measured observation yields a reward, a new immutable
    policy version, and the experiment appears in the historical outcome range."""
    from app.domain.enums import ActionType
    from app.experiments.outcomes import historical_outcome_range
    from app.learning.ingest import ingest_reward

    inc, iv, exp = await _setup(session, org)
    await record_manual_execution(session, iv.id, "alice", f.NOW)
    await session.commit()
    exp = await session.get(Experiment, exp.id, populate_existing=True)
    assert exp.dry_run is False
    session.add(Observation(experiment_id=exp.id, metrics={"visibility": 0.52}, observed_at=f.NOW + timedelta(days=3),
                            source="profound"))
    await session.commit()
    from app.core.clock import FixedClock
    from app.experiments.window import use_clock

    with use_clock(FixedClock(f.NOW + timedelta(days=30))):  # injected "now": fixtures are anchored at f.NOW
        res = await ingest_reward(session, exp.id)
    await session.commit()
    assert res.reward.total != 0 or res.reward.components
    exp = await session.get(Experiment, exp.id, populate_existing=True)
    assert exp.status == ExperimentStatus.REWARDED and exp.after_metrics
    assert len((await session.execute(select(Reward))).scalars().all()) == 1
    assert len((await session.execute(select(PolicyVersion))).scalars().all()) == 2
    rng = await historical_outcome_range(session, inc.category, ActionType.UPDATE_EXISTING_PAGE, min_n=1)
    assert rng is not None and rng.n == 1 and rng.reward is not None


async def test_manual_experiment_is_verifiable_and_never_marked_never_measured(app_client, session, org, inline_queue):
    inc, iv, exp = await _setup(session, org)
    await record_manual_execution(session, iv.id, "alice", f.NOW)
    await session.commit()
    rows = (await app_client.get("/api/experiments")).json()["items"]
    row = next(r for r in rows if r["id"] == str(exp.id))
    assert row["executor"] == "manual" and row["dry_run"] is False and row["display_status"] == "Awaiting Measurement"
    from app.core.clock import FixedClock
    from app.experiments.window import use_clock

    with use_clock(FixedClock(f.NOW + timedelta(days=30))):  # window (f.NOW + 48h) is open at the injected "now"
        v = await app_client.post(f"/api/experiments/{exp.id}/verify")
    assert v.status_code == 202, v.text  # not refused as a dry run


async def test_api_executed_endpoint_errors(app_client, session, org):
    _, iv, _ = await _setup(session, org)
    path = f"/api/interventions/{iv.id}/executed"
    h = {"X-Actor": "alice"}
    assert (await app_client.post(path, json={}, headers={"X-Actor": "system"})).status_code == 403
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert (await app_client.post(path, json={"executed_at": future}, headers=h)).status_code == 422
    ok = await app_client.post(path, json={"actual_change": "different text"}, headers=h)
    assert ok.status_code == 200 and ok.json()["deviation"] is True
    assert ok.json()["intervention"]["execution"]["deviation"] is True
    again = await app_client.post(path, json={}, headers=h)
    assert again.status_code == 409
    import uuid

    assert (await app_client.post(f"/api/interventions/{uuid.uuid4()}/executed", json={}, headers=h)).status_code == 404


async def test_api_approve_issues_package_and_cta_progression(app_client, session, org):
    inc = await f.make_incident(session, org, state=IncidentState.AWAITING_APPROVAL)
    iv = await f.make_intervention(session, inc, proposed_change=CHANGE, selected=True)
    pv = await f.make_policy_version(session)
    await ledger.open_experiment(
        session, iv, {"probability": 0.6, "action": iv.action, "scores": [], "selection_basis": "cold_start_prior",
                      "cold_start": True, "policy_version_id": pv.id}, inc, {"visibility": 0.37}, {}, [0.1])
    await session.commit()
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers={"X-Actor": "alice"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intervention"]["manual_execution_pending"] and body["intervention"]["executor"] == "manual"
    assert body["intervention"]["package"]["changes"][0]["path"] == "content/security.md"
    d = (await app_client.get(f"/api/incidents/{inc.id}")).json()
    assert d["primary_cta"]["key"] == "mark_executed" and d["primary_cta"]["label"] == "Mark as executed"
    assert {a["action"]: a["enabled"] for a in d["allowed_actions"]}["record_execution"] is True
    lst = (await app_client.get(f"/api/incidents/{inc.id}/interventions")).json()["items"][0]
    assert lst["package"]["steps"] and lst["execution_target"] == "https://testco.example/enterprise/security"
    # explicit activation is idempotent for the manual executor (no second package)
    assert (await app_client.post(f"/api/interventions/{iv.id}/execute")).status_code == 202
    assert len((await session.execute(select(Execution))).scalars().all()) == 1
    ok = await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers={"X-Actor": "alice"})
    assert ok.status_code == 200 and ok.json()["incident_state"] == "awaiting_verification"
    d = (await app_client.get(f"/api/incidents/{inc.id}")).json()
    assert d["primary_cta"]["label"] == "Awaiting Measurement" and d["primary_cta"]["enabled"] is False
    # approving with an optional executor that is not configured is refused BEFORE anything is decided
    inc2 = await f.make_incident(session, org, state=IncidentState.AWAITING_APPROVAL)
    iv2 = await f.make_intervention(session, inc2, proposed_change=CHANGE, selected=True)
    r = await app_client.post(f"/api/interventions/{iv2.id}/approve", json={"executor": "github"})
    assert r.status_code == 409 and r.json()["error"]["details"]["code"] == "github_not_configured"
    assert (await session.execute(select(Execution).where(Execution.intervention_id == iv2.id))).first() is None
