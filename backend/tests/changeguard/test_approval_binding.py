"""Own interventions run the guard when proposed and at approve; approvals bind to the action digest."""
from __future__ import annotations

import copy

import pytest
from app.domain.enums import ApprovalStatus
from app.domain.errors import ApprovalDigestMismatch
from app.models.changeguard import ChangeCheck, ChangeSet
from app.models.core import AuditEvent, Incident
from app.models.interventions import Approval, Execution
from app.services import approvals as appr
from sqlalchemy import func, select, text

from tests import factories as f
from tests.changeguard import conftest as cgc
from tests.changeguard.conftest import PAGE, H, protecting_experiment
from tests.reliability.helpers import CHANGE, MODIFIED, OBSERVE, proposed_intervention


async def count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar()


def err(r):
    return r.json()["error"]


async def approvals_of(session, iv):
    await session.rollback()
    return (await session.execute(select(Approval).where(Approval.intervention_id == iv.id))).scalars().all()


async def test_verdict_is_null_until_checked_then_stored_and_shown_on_the_intervention(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.get(f"/api/interventions/{iv.id}")
    assert r.status_code == 200 and r.json()["change_guard"] is None  # unavailable, never invented
    v = await app_client.post(f"/api/interventions/{iv.id}/change-check")
    assert v.status_code == 200, v.text
    g = v.json()
    assert g["decision"] == "ALLOW" and g["blocks_approval"] is False and g["requires_review_reason"] is False
    assert g["stale"] is False and g["semantic_check"] == "skipped_no_canonical_truth" and len(g["digest"]) == 64
    got = (await app_client.get(f"/api/interventions/{iv.id}")).json()["change_guard"]
    assert got["check_id"] == g["check_id"]
    listing = (await app_client.get(f"/api/incidents/{inc.id}/interventions")).json()["items"][0]
    assert listing["change_guard"]["decision"] == "ALLOW"
    cs = (await session.execute(select(ChangeSet))).scalars().one()
    assert cs.origin == "intervention" and cs.agent_id == "aeo-sre" and cs.source_mode == "LIVE"
    assert cs.intervention_id == iv.id and cs.target_url == "https://testco.example/enterprise/security"


async def test_approval_binds_the_digest_and_the_flow_still_works(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"note": "go"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["intervention"]["change_guard"]["decision"] == "ALLOW"
    (a,) = await approvals_of(session, iv)
    check = (await session.execute(select(ChangeCheck))).scalars().one()
    assert a.status == ApprovalStatus.APPROVED and a.action_digest == check.action_digest
    assert await count(session, Execution) == 1  # manual package issued: activation passed the digest revalidation
    ex = await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers=H)
    assert ex.status_code == 200, ex.text
    events = (await session.execute(select(AuditEvent).where(AuditEvent.event == "intervention.approved"))).scalars().all()
    assert events[0].metadata_["change_guard"]["digest"] == a.action_digest


async def test_edit_after_approval_makes_activation_and_manual_execution_409(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    iv_id, exp_id = iv.id, exp.id
    assert (await app_client.post(f"/api/interventions/{iv_id}/approve", headers=H)).status_code == 200
    changed = copy.deepcopy(CHANGE)
    changed["files"][0]["new_content"] += "\nSAML SSO now also includes SCIM provisioning."
    await session.rollback()
    await session.execute(text("update interventions set proposed_change = cast(:c as jsonb) where id=:i"),
                          {"c": __import__("json").dumps(changed), "i": iv_id})
    await session.commit()
    for path, payload in (("execute", {}), ("executed", {})):
        r = await app_client.post(f"/api/interventions/{iv_id}/{path}", json=payload, headers=H)
        assert r.status_code == 409 and err(r)["code"] == "APPROVAL_DIGEST_MISMATCH", (path, r.text)
        d = err(r)["details"]
        assert d["approved_digest"] and d["current_digest"] and d["approved_digest"] != d["current_digest"]
    # direct service call is guarded as well (executors, worker, record_manual_execution all use this gate)
    session.expire_all()
    from app.models.interventions import Intervention

    fresh = await session.get(Intervention, iv_id)
    with pytest.raises(ApprovalDigestMismatch):
        await appr.require_executable_approval(session, fresh)
    exp_row = (await session.execute(text("select status from experiments where id=:i"), {"i": exp_id})).scalar()
    assert exp_row == "approved"  # never reached executed / awaiting_verification


async def test_restoring_the_exact_change_makes_the_old_approval_valid_again(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    await session.execute(text("update interventions set proposed_change = cast(:c as jsonb) where id=:i"),
                          {"c": __import__("json").dumps({**CHANGE, "title": "x", "files": []}), "i": iv.id})
    await session.commit()
    assert (await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers=H)).status_code == 409
    await session.execute(text("update interventions set proposed_change = cast(:c as jsonb) where id=:i"),
                          {"c": __import__("json").dumps(CHANGE), "i": iv.id})
    await session.commit()
    assert (await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers=H)).status_code == 200


async def test_a_decided_approvals_digest_cannot_be_rebound(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    (a,) = await approvals_of(session, iv)
    from app.models.interventions import ImmutableApprovalError

    a.action_digest = "0" * 64
    with pytest.raises(ImmutableApprovalError):
        await session.flush()
    await session.rollback()


async def test_modify_binds_to_the_modified_change(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/modify", json={"modified_change": MODIFIED, "note": "tweak"},
                              headers=H)
    assert r.status_code == 200, r.text
    (a,) = await approvals_of(session, iv)
    assert a.status == ApprovalStatus.MODIFIED and a.action_digest
    cs = (await session.execute(select(ChangeSet))).scalars().one()
    assert "human edit" in (cs.proposed_text or "")  # the guard evaluated the MODIFIED text, not the proposal
    assert (await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers=H)).status_code == 200


# ---------------------------------------------------------------- refusals at approve
async def test_delay_refuses_approve_and_nothing_is_recorded(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    _, _, other = await protecting_experiment(session, org, target="https://testco.example/enterprise/security")
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"review_reason": "please"}, headers=H)
    assert r.status_code == 409 and err(r)["code"] == "CHANGE_GUARD_BLOCKED", r.text
    d = err(r)["details"]
    assert d["decision"] == "DELAY" and d["eligible_after"] and d["findings"][0]["references"]["experiment_code"] == other.code
    assert await approvals_of(session, iv) == []
    assert (await session.get(Incident, inc.id, populate_existing=True)).state == "awaiting_approval"
    refused = (await session.execute(select(AuditEvent).where(AuditEvent.event == "intervention.approval_refused"))).scalars().all()
    assert len(refused) == 1
    again = await app_client.post(f"/api/interventions/{iv.id}/modify", json={"modified_change": MODIFIED}, headers=H)
    assert again.status_code == 409  # modify cannot dodge the guard either
    shown = (await app_client.get(f"/api/interventions/{iv.id}")).json()["change_guard"]
    assert shown["decision"] == "DELAY" and shown["blocks_approval"] is True


async def test_block_by_canonical_truth_refuses_approve(app_client, org, session):
    from app.changeguard import canonical

    await canonical.create_claim(session, org.id, "alice@testco.example", key="saml",
                                 statement="SAML SSO is not supported.")
    await session.commit()
    inc, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert r.status_code == 409 and err(r)["details"]["decision"] == "BLOCK", r.text
    assert any(x["type"] == "canonical_conflict" for x in err(r)["details"]["findings"])


async def test_require_review_needs_a_typed_reason(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    from app.changeguard import service as cg

    sub = await cg.submit(session, cg.ChangeInput(
        org_id=org.id, agent_id="agent-9", agent_name="Other", source_mode="SIMULATED",
        target_url="https://testco.example/enterprise/security", action_type="create_faq",
        proposed_claims=["FAQ about SAML."]))
    await session.commit()
    assert sub.check.decision == "REQUIRE_REVIEW" or sub.check.decision == "ALLOW"
    v = (await app_client.post(f"/api/interventions/{iv.id}/change-check")).json()
    assert v["decision"] == "REQUIRE_REVIEW" and v["requires_review_reason"] is True and v["blocks_approval"] is False
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={}, headers=H)
    assert r.status_code == 409 and err(r)["code"] == "CHANGE_GUARD_BLOCKED"
    assert err(r)["details"]["requires_review_reason"] is True
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"review_reason": "  "}, headers=H)
    assert r.status_code == 409
    ok = await app_client.post(f"/api/interventions/{iv.id}/approve",
                               json={"review_reason": "FAQ is a different page section; reviewed"}, headers=H)
    assert ok.status_code == 200, ok.text
    ev = (await session.execute(select(AuditEvent).where(AuditEvent.event == "intervention.approved"))).scalars().one()
    assert ev.metadata_["change_guard"]["review_reason"].startswith("FAQ is a different")


async def test_external_change_that_duplicates_the_intervention_is_told_to_merge_into_it(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    from app.changeguard import service as cg

    sub = await cg.submit(session, cg.ChangeInput(
        org_id=org.id, agent_id="agent-9", agent_name="Other", source_mode="SIMULATED",
        target_url="https://testco.example/enterprise/security", action_type="update_existing_page",
        proposed_claims=["SAML SSO is supported."]))
    await session.commit()
    assert sub.check.decision == "MERGE", sub.check.findings
    mp = sub.check.merged_proposal
    assert {"kind": "intervention", "id": str(iv.id), "agent_id": "aeo-sre"} in mp["merged_from"]
    v = (await app_client.post(f"/api/interventions/{iv.id}/change-check")).json()
    assert v["decision"] == "ALLOW"  # the intervention is the change others merge INTO; it is not blocked by them
    assert (await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)).status_code == 200


async def test_observe_is_checked_but_never_blocked_by_other_or_own_experiments(app_client, org, session):
    cl = await f.make_prompt_cluster(session, org)
    await protecting_experiment(session, org, target="https://testco.example/other", cluster=cl)
    inc, iv, exp = await proposed_intervention(session, org, action="observe", change=OBSERVE)
    inc.prompt_cluster_id = cl.id  # same cluster as the protecting experiment
    await session.commit()
    v = (await app_client.post(f"/api/interventions/{iv.id}/change-check")).json()
    assert v["decision"] == "ALLOW" and v["blocks_approval"] is False
    assert any(x["type"] == "active_experiment_contamination" and x["decision"] == "ALLOW" for x in v["findings"])
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert r.status_code == 200, r.text


async def test_non_observe_same_cluster_is_delayed_like_b4_collision(app_client, org, session):
    cl = await f.make_prompt_cluster(session, org)
    _, _, other = await protecting_experiment(session, org, target="https://testco.example/other", cluster=cl)
    inc, iv, exp = await proposed_intervention(session, org)
    inc.prompt_cluster_id = cl.id
    await session.commit()
    v = (await app_client.post(f"/api/interventions/{iv.id}/change-check")).json()
    assert v["decision"] == "DELAY" and v["blocks_approval"] is True
    d = next(x for x in v["findings"] if x["type"] == "active_experiment_contamination")
    assert d["details"]["cluster_overlap_basis"] == "cluster_id" and d["references"]["experiment_code"] == other.code


async def test_own_experiment_never_blocks_its_own_intervention(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    await session.execute(text("update experiments set status='awaiting_verification', executed_at=:n, "
                               "verification_window_start=:n, verification_window_end=:n where id=:i"),
                          {"i": exp.id, "n": f.NOW})
    await session.commit()
    v = (await app_client.post(f"/api/interventions/{iv.id}/change-check")).json()
    assert v["decision"] == "ALLOW"


async def test_stale_verdict_is_flagged_when_the_proposal_changes(app_client, org, session):
    inc, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/change-check")
    await session.execute(text("update interventions set proposed_change = cast(:c as jsonb) where id=:i"),
                          {"c": __import__("json").dumps({**CHANGE, "summary": "x", "files": []}), "i": iv.id})
    await session.commit()
    assert (await app_client.get(f"/api/interventions/{iv.id}")).json()["change_guard"]["stale"] is True


async def test_propose_pipeline_runs_the_guard(session, org, monkeypatch):
    """pipeline.propose calls the guard after opening the approval; here via the helper it uses."""
    from app.services import pipeline

    inc, iv, exp = await proposed_intervention(session, org)
    await pipeline._guard_proposal(session, iv)
    assert await count(session, ChangeCheck) == 1
    monkeypatch.setattr("app.changeguard.service.evaluate_intervention", _boom)
    await pipeline._guard_proposal(session, iv)  # a guard failure never fails the proposal
    assert await count(session, ChangeCheck) == 1


async def _boom(*a, **k):
    raise RuntimeError("guard exploded")


def test_cgc_module_imports():
    assert cgc.TOKEN and cgc.PAGE == PAGE
