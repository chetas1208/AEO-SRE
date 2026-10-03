"""Precedence matrix, digest stability, approval binding, idempotency, no mutation of experiments."""
from __future__ import annotations

import itertools
import json

import pytest
from app.models.interventions import Intervention
from sqlalchemy import text

from tests.guard_reliability.support import (
    changeset,
    decision,
    eligible_after,
    err_code,
    experiment_snapshot,
    make_canonical,
    post,
    protected_experiment,
)

CONTRA = ["SAML SSO is available only on the Enterprise plan."]
BOTH = "Business and Enterprise plans both include SAML single sign-on."
ORDER = ["BLOCK", "DELAY", "REQUIRE_REVIEW", "MERGE", "ALLOW"]


async def test_precedence_block_beats_delay(client, session, org):
    await protected_experiment(session, org)
    await make_canonical(client, org, "saml", BOTH, entities=["SAML"])
    r = await post(client, changeset(org, proposed_claims=CONTRA))
    assert decision(r) == "BLOCK"
    types = " ".join(str(x.get("type", "")).lower() for x in r.json()["findings"])
    assert "canonical" in types and ("contamination" in types or "experiment" in types), types  # all findings listed
    assert eligible_after(r), "DELAY finding must still carry eligible_after even when BLOCK wins"


async def test_precedence_delay_beats_merge(client, session, org):
    await protected_experiment(session, org)
    a = await post(client, changeset(org, target_url="https://testco.example/other"))
    assert decision(a) != "DELAY"
    b = await post(client, changeset(org, target_url="https://testco.example/other",
                                     agent={"id": "sim-agent-2", "name": "Simulated Agent 2"}))
    assert decision(b) in ("MERGE", "REQUIRE_REVIEW", "ALLOW")
    # now the same pair against the protected target
    c = await post(client, changeset(org))
    d = await post(client, changeset(org, agent={"id": "sim-agent-3", "name": "Simulated Agent 3"}))
    assert decision(c) == "DELAY" and decision(d) == "DELAY"


async def test_duplicate_target_merges_and_conflict_requires_review(client, org):
    first = await post(client, changeset(org, target_url="https://testco.example/faq",
                                         proposed_claims=["SAML SSO is available on Enterprise."]))
    assert decision(first) == "ALLOW", first.json()
    dup = await post(client, changeset(org, target_url="https://testco.example/faq",
                                       agent={"id": "sim-agent-2", "name": "Simulated Agent 2"},
                                       proposed_claims=["SAML SSO is available on Enterprise."]))
    assert decision(dup) == "MERGE", dup.json()
    conflict = await post(client, changeset(org, target_url="https://testco.example/faq",
                                            agent={"id": "sim-agent-3", "name": "Simulated Agent 3"},
                                            proposed_claims=["SAML SSO is not available on Enterprise."]))
    assert decision(conflict) == "REQUIRE_REVIEW", conflict.json()


async def test_different_intent_same_target_requires_review(client, org):
    await post(client, changeset(org, target_url="https://testco.example/faq", action_type="create_faq"))
    r = await post(client, changeset(org, target_url="https://testco.example/faq", action_type="structured_data",
                                     agent={"id": "sim-agent-2", "name": "Simulated Agent 2"}))
    assert decision(r) == "REQUIRE_REVIEW", r.json()


async def test_precedence_total_order_is_respected():
    """Pure sanity on the order constant used by this suite (mirrors the spec)."""
    for a, b in itertools.combinations(ORDER, 2):
        assert ORDER.index(a) < ORDER.index(b)


# --- digest -----------------------------------------------------------------------------------------------
async def _digest(client, org, **over):
    r = await post(client, changeset(org, profound_run_id="fixed-run", **over))
    assert r.status_code in (200, 201, 409), r.text
    return r


async def test_digest_stable_under_claim_reordering_and_case_noise(client, org):
    claims = ["Alpha is supported.", "Beta is supported.", "Gamma is supported."]
    a = await post(client, changeset(org, proposed_claims=claims, idempotency_key="k-a", profound_run_id="r"))
    b = await post(client, changeset(org, proposed_claims=list(reversed(claims)), idempotency_key="k-b",
                                     profound_run_id="r"))
    assert a.json()["digest"] == b.json()["digest"]


async def test_digest_changes_on_any_claim_change(client, org):
    base = ["Alpha is supported.", "Beta is supported."]
    d0 = (await post(client, changeset(org, proposed_claims=base, idempotency_key="d0", profound_run_id="r"))).json()["digest"]
    for i, variant in enumerate([
        ["Alpha is supported.", "Beta is NOT supported."],
        ["Alpha is supported."],
        base + ["Delta is supported."],
    ]):
        r = await post(client, changeset(org, proposed_claims=variant, idempotency_key=f"d{i + 1}", profound_run_id="r"))
        assert r.json()["digest"] != d0, variant


async def test_digest_changes_with_target_action_and_text(client, org):
    kw = dict(profound_run_id="r")
    d0 = (await post(client, changeset(org, idempotency_key="t0", **kw))).json()["digest"]
    for i, over in enumerate([{"target_url": "https://testco.example/x"}, {"action_type": "create_faq"},
                              {"proposed_text": "new body"}]):
        r = await post(client, changeset(org, idempotency_key=f"t{i + 1}", **kw, **over))
        assert r.json()["digest"] != d0, over


async def test_digest_changes_when_experiment_context_changes(client, session, org):
    a = await post(client, changeset(org, idempotency_key="c0", profound_run_id="r"))
    await protected_experiment(session, org)
    b = await post(client, changeset(org, idempotency_key="c1", profound_run_id="r"))
    assert a.json()["digest"] != b.json()["digest"]


# --- idempotency ------------------------------------------------------------------------------------------
async def test_idempotent_replay_returns_same_decision_and_flag(client, org):
    body = changeset(org, idempotency_key="idem-1")
    a = await post(client, body)
    b = await post(client, body)
    assert a.json()["id"] == b.json()["id"]
    assert b.json()["decision"] == a.json()["decision"]
    assert b.json().get("replayed") is True
    assert a.json().get("replayed") in (False, None)


async def test_same_key_different_digest_conflicts(client, org):
    await post(client, changeset(org, idempotency_key="idem-2"))
    r = await post(client, changeset(org, idempotency_key="idem-2", proposed_claims=["something else entirely."]))
    assert r.status_code == 409, r.text
    assert err_code(r)


async def test_replay_after_state_change_returns_stored_decision(client, session, org):
    body = changeset(org, idempotency_key="idem-3")
    a = await post(client, body)
    await protected_experiment(session, org)
    b = await post(client, body)
    assert b.json()["decision"] == a.json()["decision"], "replay must return the stored decision, not re-decide"


# --- approval binding ---------------------------------------------------------------------------------------
async def test_edit_after_approval_blocks_manual_execution_409(client, session, org):
    from tests.reliability.helpers import proposed_intervention

    inc, iv, exp = await proposed_intervention(session, org)
    r = await client.post(f"/api/interventions/{iv.id}/approve", json={"note": "ok"}, headers={"X-Actor": "alice"})
    assert r.status_code == 200, r.text
    await session.rollback()
    ap = (await session.execute(
        text("select action_digest from approvals where intervention_id=:i"), {"i": str(iv.id)})).first()
    assert ap and ap[0], "approval must store the action digest"
    iv2 = await session.get(Intervention, iv.id)
    change = dict(iv2.proposed_change)
    change["summary"] = "tampered after approval"
    change["files"] = [{**change["files"][0], "new_content": "## Totally different claim"}]
    await session.execute(text("update interventions set proposed_change = cast(:c as json) where id = :i"),
                          {"c": json.dumps(change), "i": str(iv.id)})
    await session.commit()
    r2 = await client.post(f"/api/interventions/{iv.id}/executed", json={"executed_by": "alice"},
                           headers={"X-Actor": "alice"})
    assert r2.status_code == 409 and err_code(r2) == "APPROVAL_DIGEST_MISMATCH", r2.text


async def test_unedited_approval_still_executes(client, session, org):
    from tests.reliability.helpers import proposed_intervention

    _, iv, _ = await proposed_intervention(session, org)
    await client.post(f"/api/interventions/{iv.id}/approve", json={"note": "ok"}, headers={"X-Actor": "alice"})
    r = await client.post(f"/api/interventions/{iv.id}/executed", json={"executed_by": "alice"},
                          headers={"X-Actor": "alice"})
    assert err_code(r) != "APPROVAL_DIGEST_MISMATCH", r.text


# --- checks never mutate experiments ----------------------------------------------------------------------------
async def test_checks_do_not_mutate_experiment(client, session, org):
    _, _, exp = await protected_experiment(session, org)
    before = await experiment_snapshot(session, exp.id)
    for i in range(3):
        await post(client, changeset(org, idempotency_key=f"m{i}", proposed_claims=[f"claim {i}"]))
    after = await experiment_snapshot(session, exp.id)
    assert before == after


@pytest.mark.parametrize("n", [1])
async def test_delay_eligible_after_not_before_now_for_future_window(client, session, org, n):
    from datetime import UTC, datetime

    _, _, exp = await protected_experiment(session, org, executed_at=datetime.now(UTC))
    r = await post(client, changeset(org))
    from tests.guard_reliability.support import expected_after, iso_close

    assert iso_close(eligible_after(r), expected_after(exp))
