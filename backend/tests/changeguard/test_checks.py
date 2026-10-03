"""The three checks + precedence, through the service (real Postgres test DB, pinned clock)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.changeguard import canonical
from app.changeguard import service as cg
from app.domain.errors import ChangeCheckKeyConflict, ChangeSetInvalid, OrganizationNotFound
from app.models.changeguard import ChangeCheck, ChangeSet
from sqlalchemy import func, select

from tests import factories as f
from tests.changeguard.conftest import PAGE, protecting_experiment


def inp(org, **kw):
    base = dict(org_id=org.id, agent_id="agent-7", agent_name="Content Agent", source_mode="SIMULATED",
                target_url="https://testco.example/pricing", action_type="update_existing_page",
                proposed_claims=["SAML SSO is available on the Enterprise plan."], reason="r")
    base.update(kw)
    return cg.ChangeInput(**base)


async def run(session, org, **kw):
    sub = await cg.submit(session, inp(org, **kw))
    await session.commit()
    return sub.check, sub


def types(check):
    return {x["type"] for x in check.findings}


def finding(check, typ):
    return next(x for x in check.findings if x["type"] == typ)


# ---------------------------------------------------------------- check 1: active-experiment contamination
async def test_no_experiment_no_canonical_is_allow_and_never_a_silent_pass(session, org):
    check, _ = await run(session, org)
    assert check.decision == "ALLOW"
    assert check.semantic_check == "skipped_no_canonical_truth"
    assert "canonical_truth_unavailable" in types(check)  # reported, not silently passed
    assert check.guard_version.startswith("change-guard/")


async def test_exact_target_overlap_delays_until_the_window_opens(session, org):
    _, _, exp = await protecting_experiment(session, org)  # executed 1h before NOW, delay 48h
    check, _ = await run(session, org, target_url=PAGE)
    assert check.decision == "DELAY"
    fd = finding(check, "active_experiment_contamination")
    assert fd["references"]["experiment_code"] == exp.code and fd["details"]["target_overlap_pct"] == 100.0
    assert fd["details"]["eligible_after_basis"] == "window_start"
    assert cg.vwindow.aware(check.eligible_after) == cg.vwindow.aware(exp.verification_window_start)
    assert exp.code in check.experiment_refs and str(exp.id) in check.experiment_refs
    assert check.experiment_context == [{"experiment_id": str(exp.id), "status": "awaiting_verification"}]


async def test_path_prefix_overlap_both_directions(session, org):
    await protecting_experiment(session, org, target="https://testco.example/enterprise/security")
    for target in ("https://testco.example/enterprise", "https://testco.example/enterprise/security/saml"):
        check, _ = await run(session, org, target_url=target, proposed_claims=[])
        assert check.decision == "DELAY", target
        assert finding(check, "active_experiment_contamination")["details"]["matched_targets"][0]["match"] == "prefix"
    check, _ = await run(session, org, target_url="https://testco.example/enterprises", proposed_claims=[])
    assert check.decision == "ALLOW"


async def test_no_overlap_allows(session, org):
    await protecting_experiment(session, org)
    check, _ = await run(session, org, target_url="https://testco.example/blog/other", proposed_claims=[])
    assert check.decision == "ALLOW" and "active_experiment_contamination" not in types(check)


async def test_prompt_cluster_overlap_delays_even_without_target_overlap(session, org):
    cl = await f.make_prompt_cluster(session, org, "Enterprise SSO", ["best sso tool"])
    await protecting_experiment(session, org, target="https://testco.example/enterprise/security", cluster=cl)
    check, _ = await run(session, org, target_url="https://testco.example/blog/x", proposed_claims=[],
                         prompt_cluster_ids=[str(cl.id)])
    assert check.decision == "DELAY"
    d = finding(check, "active_experiment_contamination")["details"]
    assert d["prompt_cluster_overlap_pct"] == 100.0 and d["cluster_overlap_basis"] == "cluster_id"
    assert d["target_overlap_pct"] == 0.0
    check, _ = await run(session, org, target_url="https://testco.example/blog/x", proposed_claims=[],
                         prompts=["best SSO tool"], idempotency_key="k-prompts")
    assert check.decision == "DELAY" and finding(
        check, "active_experiment_contamination")["details"]["cluster_overlap_basis"] == "prompts"


async def test_observe_experiment_protects_its_owned_pages_and_is_reported_as_observe(session, org):
    inc, iv, exp = await protecting_experiment(session, org, target=None, selected_action="observe")
    await f.make_evidence(session, inc, url="https://testco.example/docs/sso")
    check, _ = await run(session, org, target_url="https://testco.example/docs/sso/", proposed_claims=[])
    assert check.decision == "DELAY"
    d = finding(check, "active_experiment_contamination")["details"]
    assert d["observe_baseline"] is True and "OBSERVE baseline" in finding(
        check, "active_experiment_contamination")["reason"]


async def test_other_org_and_finished_or_dry_run_experiments_do_not_protect(session, org):
    other = await f.make_org(session)
    await protecting_experiment(session, other)
    await protecting_experiment(session, org, status="verified")
    await protecting_experiment(session, org, dry_run=True)
    check, _ = await run(session, org, target_url=PAGE, proposed_claims=[])
    assert check.decision == "ALLOW"


async def test_window_open_but_unverified_reports_window_end(session, org):
    _, _, exp = await protecting_experiment(session, org, executed_at=f.NOW - timedelta(hours=60))  # opened 12h ago
    check, _ = await run(session, org, target_url=PAGE, proposed_claims=[])
    d = finding(check, "active_experiment_contamination")
    assert d["details"]["eligible_after_basis"] == "window_end"
    assert cg.vwindow.aware(check.eligible_after) == cg.vwindow.aware(exp.verification_window_end)


async def test_overdue_verification_requires_review_never_a_delay_without_a_time(session, org):
    await protecting_experiment(session, org, executed_at=f.NOW - timedelta(days=30))
    check, _ = await run(session, org, target_url=PAGE, proposed_claims=[])
    assert check.decision == "REQUIRE_REVIEW" and check.eligible_after is None
    assert "overdue" in finding(check, "active_experiment_contamination")["reason"]
    assert finding(check, "active_experiment_contamination")["details"]["eligible_after_basis"] == "verification_overdue"


async def test_executing_experiment_without_window_uses_earliest_possible_start_from_the_window_service(session, org):
    _, _, exp = await protecting_experiment(session, org, status="executing")
    await session.execute(__import__("sqlalchemy").text(
        "update experiments set verification_window_start=null, verification_window_end=null where id=:i"),
        {"i": exp.id})
    await session.commit()
    await session.refresh(exp)
    check, _ = await run(session, org, target_url=PAGE, proposed_claims=[])
    assert check.decision == "DELAY"
    d = finding(check, "active_experiment_contamination")["details"]
    assert d["eligible_after_basis"] == "earliest_possible_window_start"
    assert cg.vwindow.aware(check.eligible_after) == f.NOW + timedelta(hours=48)


async def test_observe_action_is_checked_but_never_delayed(session, org):
    await protecting_experiment(session, org)
    check, _ = await run(session, org, target_url=PAGE, action_type="observe", proposed_claims=[])
    assert check.decision == "ALLOW"
    fd = finding(check, "active_experiment_contamination")
    assert fd["decision"] == "ALLOW" and fd["eligible_after"] is None


# ---------------------------------------------------------------- check 2: duplicate / conflicting changes
async def test_identical_pending_change_from_another_agent_is_merged(session, org):
    a, _ = await run(session, org, agent_id="agent-1", agent_name="A")
    assert a.decision == "ALLOW"
    b, _ = await run(session, org, agent_id="agent-2", agent_name="B")
    assert b.decision == "MERGE"
    fd = finding(b, "duplicate_target_change")
    assert fd["references"]["change_set_id"] and fd["details"]["claims_relation"] == "identical"
    mp = b.merged_proposal
    assert mp["target_url"] == "https://testco.example/pricing"
    assert mp["proposed_claims"] == ["saml sso is available on the enterprise plan"]
    assert len(mp["merged_from"]) == 2


async def test_subset_claims_merge_into_the_union(session, org):
    await run(session, org, agent_id="agent-1", proposed_claims=["Claim one is true.", "Claim two is true."])
    b, _ = await run(session, org, agent_id="agent-2", proposed_claims=["Claim one is true."])
    assert b.decision == "MERGE" and b.merged_proposal["proposed_claims"] == ["claim one is true", "claim two is true"]


async def test_different_action_types_on_the_same_target_require_review(session, org):
    await run(session, org, agent_id="agent-1")
    b, _ = await run(session, org, agent_id="agent-2", action_type="create_faq")
    assert b.decision == "REQUIRE_REVIEW" and b.merged_proposal is None
    d = finding(b, "conflicting_target_change")["details"]
    assert d["other_action"] == "update_existing_page" and d["target_match"] == "exact"


async def test_conflicting_claims_on_the_same_target_require_review(session, org):
    await run(session, org, agent_id="agent-1", proposed_claims=["SAML SSO is available on the Pro plan."])
    b, _ = await run(session, org, agent_id="agent-2",
                     proposed_claims=["SAML SSO is not available on the Pro plan."])
    assert b.decision == "REQUIRE_REVIEW"
    assert finding(b, "conflicting_target_change")["details"]["claims_relation"] == "conflicting"


async def test_unrelated_non_conflicting_claims_merge_into_the_union(session, org):
    await run(session, org, agent_id="agent-1", proposed_claims=["We were founded in a garage."])
    b, _ = await run(session, org, agent_id="agent-2", proposed_claims=["Our logo is blue."])
    assert b.decision == "MERGE"
    assert b.merged_proposal["proposed_claims"] == ["our logo is blue", "we were founded in a garage"]


async def test_different_target_or_blocked_or_old_changes_are_not_pending(session, org, monkeypatch):
    await run(session, org, agent_id="agent-1", target_url="https://testco.example/other")
    b, _ = await run(session, org, agent_id="agent-2")
    assert b.decision == "ALLOW"
    await protecting_experiment(session, org, target="https://testco.example/blocked-page")
    d, _ = await run(session, org, agent_id="agent-3", target_url="https://testco.example/blocked-page")
    assert d.decision == "DELAY"
    e, _ = await run(session, org, agent_id="agent-4", target_url="https://testco.example/blocked-page",
                     idempotency_key="k4")
    assert e.decision == "DELAY"  # a DELAYed change is not "pending": it was told to stop
    with cg.vwindow.use_clock(__import__("app.core.clock", fromlist=["x"]).FixedClock(f.NOW + timedelta(days=30))):
        stale, _ = await run(session, org, agent_id="agent-5", idempotency_key="k5")
    assert stale.decision == "ALLOW"  # agent-1/2's changes are older than the pending TTL (72h)


async def test_own_pending_intervention_on_the_same_target_is_compared(session, org):
    from tests.reliability.helpers import proposed_intervention

    inc, iv, exp = await proposed_intervention(session, org)  # targets https://testco.example/enterprise/security
    check, _ = await run(session, org, target_url=PAGE, action_type="update_existing_page",
                         proposed_claims=["SAML SSO is supported."])
    fd = next(x for x in check.findings if x["type"] in ("duplicate_target_change", "conflicting_target_change"))
    assert fd["references"]["intervention_id"] == str(iv.id) and fd["references"]["agent_id"] == "aeo-sre"
    other, _ = await run(session, org, target_url=PAGE, action_type="create_faq", idempotency_key="faq")
    assert other.decision == "REQUIRE_REVIEW"


# ---------------------------------------------------------------- check 3: canonical truth
async def add_claim(session, org, key="saml-plan", statement="SAML SSO is only available on the Enterprise plan.",
                    **kw):
    c = await canonical.create_claim(session, org.id, "alice@testco.example", key=key, statement=statement, **kw)
    await session.commit()
    return c


async def test_contradicting_claim_is_blocked_with_the_canonical_reference(session, org):
    claim = await add_claim(session, org)
    check, _ = await run(session, org, proposed_claims=["SAML SSO is available on the Pro plan."])
    assert check.decision == "BLOCK"
    fd = finding(check, "canonical_conflict")
    assert fd["references"]["canonical_claim_id"] == str(claim.id) and fd["references"]["canonical_claim_key"] == "saml-plan"
    assert fd["decision"] == "BLOCK" and fd["details"]["relation"] == "CONFLICTING"
    assert check.semantic_check in ("ok", "degraded")


@pytest.mark.parametrize("canon,proposed", [
    ("The Pro plan includes up to 100 seats.", "The Pro plan includes up to 500 seats."),
    ("SAML SSO is only available on the Enterprise plan.", "SAML SSO is not offered on the Enterprise plan."),
    ("The Pro plan costs $49 per month.", "The Pro plan costs $99 per month."),
])
async def test_rule_conflicts_block(session, org, canon, proposed):
    await add_claim(session, org, statement=canon)
    check, _ = await run(session, org, proposed_claims=[proposed])
    assert check.decision == "BLOCK", check.findings


async def test_compatible_claim_is_not_blocked(session, org):
    await add_claim(session, org)
    check, _ = await run(session, org, proposed_claims=["SAML SSO is available on the Enterprise plan."])
    assert check.decision == "ALLOW" and "canonical_conflict" not in types(check)


async def test_degraded_semantic_state_is_reported_when_model_and_ranker_are_unavailable(session, org):
    await add_claim(session, org)
    check, _ = await run(session, org, proposed_claims=["SAML SSO is available on the Enterprise plan."])
    assert check.semantic_check == "degraded"  # no model configured in tests: rules alone decided
    assert "semantic_check_degraded" in types(check)


async def test_retired_canonical_claims_and_other_org_claims_are_ignored(session, org):
    c = await add_claim(session, org)
    other = await f.make_org(session)
    await add_claim(session, other)
    await canonical.update_claim(session, await canonical.get_claim(session, org.id, c.id),
                                 "alice@testco.example", retire=True)
    await session.commit()
    check, _ = await run(session, org, proposed_claims=["SAML SSO is available on the Pro plan."])
    assert check.decision == "ALLOW" and check.semantic_check == "skipped_no_canonical_truth"


async def test_url_scoped_claim_applies_only_to_that_section(session, org):
    await add_claim(session, org, scope="https://testco.example/enterprise")
    check, _ = await run(session, org, target_url="https://testco.example/blog/post",
                         proposed_claims=["SAML SSO is available on the Pro plan."])
    assert check.decision == "ALLOW"
    check, _ = await run(session, org, target_url="https://testco.example/enterprise/security",
                         proposed_claims=["SAML SSO is available on the Pro plan."], idempotency_key="scoped")
    assert check.decision == "BLOCK"


async def test_claims_inside_proposed_text_are_checked_too(session, org):
    await add_claim(session, org)
    check, _ = await run(session, org, proposed_claims=[],
                         proposed_text="Pricing update.\nSAML SSO is available on the Pro plan.")
    assert check.decision == "BLOCK"


# ---------------------------------------------------------------- precedence over several checks
async def test_block_beats_delay_beats_review_beats_merge(session, org):
    await add_claim(session, org)
    await protecting_experiment(session, org)  # PAGE is protected
    await run(session, org, agent_id="agent-1", target_url="https://testco.example/pricing")
    pend_other, _ = await run(session, org, agent_id="agent-9", target_url="https://testco.example/pricing",
                              action_type="create_faq", idempotency_key="faq9")
    assert pend_other.decision == "REQUIRE_REVIEW"
    # DELAY + REQUIRE_REVIEW both fire on PAGE? first make a pending change on PAGE itself
    review_only, _ = await run(session, org, agent_id="agent-2", target_url="https://testco.example/pricing",
                               action_type="create_faq", idempotency_key="faq2")
    assert review_only.decision == "REQUIRE_REVIEW"
    # DELAY beats REQUIRE_REVIEW / MERGE
    delay, _ = await run(session, org, agent_id="agent-3", target_url=PAGE,
                         proposed_claims=["SAML SSO is available on the Enterprise plan."], idempotency_key="d3")
    assert delay.decision == "DELAY"
    # BLOCK beats DELAY, and every finding is still returned
    block, _ = await run(session, org, agent_id="agent-4", target_url=PAGE,
                         proposed_claims=["SAML SSO is available on the Pro plan."], idempotency_key="b4")
    assert block.decision == "BLOCK"
    assert {"canonical_conflict", "active_experiment_contamination"} <= types(block)
    assert block.eligible_after is not None  # the DELAY time is still reported alongside the BLOCK


# ---------------------------------------------------------------- input validation
async def test_invalid_inputs_are_typed_errors(session, org):
    with pytest.raises(ChangeSetInvalid):
        await cg.submit(session, inp(org, action_type="rewrite_everything"))
    with pytest.raises(ChangeSetInvalid):
        await cg.submit(session, inp(org, target_url="javascript:alert(1)"))
    with pytest.raises(OrganizationNotFound):
        await cg.resolve_org(session, None, "nobody.example")
    assert (await cg.resolve_org(session, None, org.domain.upper())).id == org.id


# ---------------------------------------------------------------- idempotency
async def test_same_key_same_digest_replays_the_stored_decision_without_new_rows(session, org):
    first, sub1 = await run(session, org, idempotency_key="k1")
    again = await cg.submit(session, inp(org, idempotency_key="k1"))
    await session.commit()
    assert again.replayed and again.check.id == first.id and not sub1.replayed
    n_sets = (await session.execute(select(func.count()).select_from(ChangeSet))).scalar()
    n_checks = (await session.execute(select(func.count()).select_from(ChangeCheck))).scalar()
    assert (n_sets, n_checks) == (1, 1)


async def test_same_key_different_digest_conflicts(session, org):
    await run(session, org, idempotency_key="k1")
    with pytest.raises(ChangeCheckKeyConflict) as e:
        await cg.submit(session, inp(org, idempotency_key="k1", proposed_claims=["something else entirely."]))
    assert e.value.code == "CHANGE_CHECK_KEY_CONFLICT" and e.value.details["stored_proposal_digest"]


async def test_default_key_is_agent_run_digest_so_a_resend_replays(session, org):
    a, _ = await run(session, org, profound_run_id="run-1")
    again = await cg.submit(session, inp(org, profound_run_id="run-1"))
    assert again.replayed and again.check.id == a.id
    third = await cg.submit(session, inp(org, profound_run_id="run-2"))
    assert not third.replayed


async def test_recheck_appends_a_fresh_decision_and_digest_after_the_experiment_finished(session, org):
    _, _, exp = await protecting_experiment(session, org)
    first, _ = await run(session, org, target_url=PAGE, proposed_claims=[], idempotency_key="k1")
    assert first.decision == "DELAY"
    await session.execute(__import__("sqlalchemy").text("update experiments set status='verified' where id=:i"),
                          {"i": exp.id})
    await session.commit()
    replay = await cg.submit(session, inp(org, target_url=PAGE, proposed_claims=[], idempotency_key="k1"))
    assert replay.replayed and replay.check.decision == "DELAY"  # a stored DELAY never silently turns into ALLOW
    fresh = await cg.submit(session, inp(org, target_url=PAGE, proposed_claims=[], idempotency_key="k1", recheck=True))
    await session.commit()
    assert not fresh.replayed and fresh.check.decision == "ALLOW"
    assert fresh.check.id != first.id and fresh.check.action_digest != first.action_digest
    assert fresh.change_set.id == replay.change_set.id
    n = (await session.execute(select(func.count()).select_from(ChangeCheck))).scalar()
    assert n == 2  # one row per evaluation


@pytest.mark.parametrize("variant", [
    "http://www.testco.example/enterprise/security", "https://testco.example/enterprise/%73ecurity",
    "https://testco.example/enterprise/./security", "https://testco.example/x/../enterprise/security",
    "https://TESTCO.EXAMPLE/ENTERPRISE/SECURITY/", "https://testco.example/enterprise/security#top?utm_source=x"])
async def test_target_variants_never_slip_past_a_protected_page(session, org, variant):
    await protecting_experiment(session, org)
    check, _ = await run(session, org, target_url=variant, proposed_claims=[], idempotency_key=variant)
    assert check.decision == "DELAY", variant


async def test_expired_canonical_claim_is_not_enforced(session, org):
    from datetime import timedelta as td

    await add_claim(session, org, valid_until=f.NOW - td(days=1))
    check, _ = await run(session, org, proposed_claims=["SAML SSO is available on the Pro plan."])
    assert check.decision == "ALLOW" and check.semantic_check == "skipped_no_canonical_truth"
    await add_claim(session, org, key="live", valid_until=f.NOW + td(days=1))
    check, _ = await run(session, org, proposed_claims=["SAML SSO is available on the Pro plan."], idempotency_key="b")
    assert check.decision == "BLOCK"


async def test_concurrent_contradictory_changes_do_not_both_allow(engine, org):
    import asyncio

    import app.core.db as appdb

    async def post(agent, claim):
        async with appdb.get_sessionmaker()() as s:
            sub = await cg.submit(s, inp(org, agent_id=agent, proposed_claims=[claim],
                                         target_url="https://testco.example/race"))
            await s.commit()
            return sub.check.decision

    res = await asyncio.gather(post("a1", "SAML SSO is available on the Pro plan."),
                               post("a2", "SAML SSO is not available on the Pro plan."))
    assert sorted(res) == ["ALLOW", "REQUIRE_REVIEW"], res
