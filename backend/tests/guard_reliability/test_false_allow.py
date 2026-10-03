"""False-ALLOW attacks: every variant of a protected target must still be DELAYed with a real eligible_after."""
from __future__ import annotations

import pytest

from tests.guard_reliability.support import (
    TARGET,
    changeset,
    decision,
    eligible_after,
    expected_after,
    finding_types,
    has_finding,
    iso_close,
    post,
    protected_experiment,
)

VARIANTS = {
    "exact": TARGET,
    "uppercase": TARGET.upper().replace("HTTPS://", "https://"),
    "mixed_host_case": "https://TestCo.Example/enterprise/security",
    "trailing_slash": TARGET + "/",
    "double_trailing_slash": TARGET + "//",
    "tracking_params": TARGET + "?utm_source=x&utm_medium=y&gclid=abc&fbclid=1",
    "fragment": TARGET + "#saml",
    "http_scheme": TARGET.replace("https://", "http://"),
    "www_prefix": TARGET.replace("https://", "https://www."),
    "url_encoded_path": "https://testco.example/enterprise/%73ecurity",
    "url_encoded_slash_case": "https://testco.example/Enterprise/Security",
    "default_port": "https://testco.example:443/enterprise/security",
    "dot_segments": "https://testco.example/enterprise/./security",
    "surrounding_whitespace": f"  {TARGET}  ",
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
async def test_target_variant_is_still_delayed(client, session, org, name):
    _, _, exp = await protected_experiment(session, org)
    r = await post(client, changeset(org, target_url=VARIANTS[name]))
    assert r.status_code in (200, 201), r.text
    assert decision(r) == "DELAY", (name, r.json())
    ea = eligible_after(r)
    assert ea and iso_close(ea, expected_after(exp)), (ea, expected_after(exp))
    assert has_finding(r, "contamination") or has_finding(r, "experiment"), finding_types(r)


async def test_path_prefix_section_is_delayed(client, session, org):
    _, _, exp = await protected_experiment(session, org, target="https://testco.example/enterprise")
    r = await post(client, changeset(org, target_url="https://testco.example/enterprise/security/saml"))
    assert decision(r) == "DELAY", r.json()
    assert iso_close(eligible_after(r), expected_after(exp))


async def test_parent_section_change_against_child_experiment_is_delayed(client, session, org):
    await protected_experiment(session, org)
    r = await post(client, changeset(org, target_url="https://testco.example/enterprise"))
    assert decision(r) == "DELAY", r.json()


async def test_sibling_prefix_lookalike_is_not_overmatched(client, session, org):
    """/enterprise/security-faq is NOT inside /enterprise/security (string prefix is not a path prefix)."""
    await protected_experiment(session, org)
    r = await post(client, changeset(org, target_url="https://testco.example/enterprise/security-faq"))
    assert decision(r) != "DELAY", r.json()


async def test_unrelated_target_is_allowed_with_no_findings_of_delay(client, session, org):
    await protected_experiment(session, org)
    r = await post(client, changeset(org, target_url="https://testco.example/pricing"))
    assert decision(r) in ("ALLOW", "REQUIRE_REVIEW", "BLOCK", "MERGE")
    assert decision(r) != "DELAY"
    assert not has_finding(r, "contamination")


@pytest.mark.parametrize("status", ["executing", "executed", "awaiting_verification"])
async def test_every_measurement_pending_state_protects(client, session, org, status):
    await protected_experiment(session, org, status=status)
    r = await post(client, changeset(org))
    assert decision(r) == "DELAY", (status, r.json())
    assert eligible_after(r)


@pytest.mark.parametrize("status", ["verified", "rewarded", "rejected", "failed"])
async def test_finished_experiment_does_not_protect(client, session, org, status):
    await protected_experiment(session, org, status=status)
    r = await post(client, changeset(org))
    assert decision(r) != "DELAY", (status, r.json())


async def test_observe_experiment_protects_and_is_reported_as_observe(client, session, org):
    await protected_experiment(session, org, action="observe")
    r = await post(client, changeset(org))
    assert decision(r) == "DELAY", r.json()
    assert "observe" in str(r.json()).lower()


async def test_other_org_experiment_does_not_protect(client, session, org):
    from tests import factories

    other = await factories.make_org(session)
    await protected_experiment(session, other)
    r = await post(client, changeset(org))
    assert decision(r) != "DELAY", r.json()


async def test_prompt_cluster_overlap_is_delayed(client, session, org):
    """Different target, same prompt cluster as the protected experiment. Needs the optional cluster field."""
    inc, _, exp = await protected_experiment(session, org)
    r = await post(client, changeset(org, target_url="https://testco.example/other",
                                     prompt_cluster_ids=[str(inc.prompt_cluster_id)]))
    if r.status_code == 422:
        pytest.skip("ChangeSet has no prompt_cluster_ids field in this build (see handoff-g4 note)")
    assert decision(r) == "DELAY", r.json()
    assert has_finding(r, "contamination") or has_finding(r, "cluster")


async def test_overlap_percentages_reported(client, session, org):
    await protected_experiment(session, org)
    r = await post(client, changeset(org))
    blob = str(r.json()).lower()
    assert "overlap" in blob, r.json()
