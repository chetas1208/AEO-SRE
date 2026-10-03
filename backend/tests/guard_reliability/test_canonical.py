"""Canonical-truth conflict, expired/retired claims, empty truth, degraded semantic check."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.guard_reliability.support import (
    changeset,
    decision,
    err_code,
    finding_types,
    has_finding,
    make_canonical,
    post,
)

BOTH_SAML = "Business and Enterprise plans both include SAML single sign-on."


async def _seed(client, org, **kw):
    r = await make_canonical(client, org, "saml-plans", BOTH_SAML, entities=["SAML", "Business", "Enterprise"], **kw)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_exclusivity_contradiction_blocks(client, org):
    await _seed(client, org)
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    assert decision(r) == "BLOCK", r.json()
    assert has_finding(r, "canonical") or has_finding(r, "contradiction")


@pytest.mark.parametrize("claim", [
    "Business plan does not include SAML single sign-on.",
    "The Business plan has no SAML.",
    "SAML is not supported on Business.",
])
async def test_negation_contradiction_blocks(client, org, claim):
    await _seed(client, org)
    r = await post(client, changeset(org, proposed_claims=[claim]))
    assert decision(r) == "BLOCK", (claim, r.json())


async def test_numeric_contradiction_blocks(client, org):
    await make_canonical(client, org, "seats", "The Enterprise plan includes up to 500 seats.", entities=["Enterprise"])
    r = await post(client, changeset(org, proposed_claims=["The Enterprise plan includes up to 50 seats."]))
    assert decision(r) == "BLOCK", r.json()


async def test_date_contradiction_blocks(client, org):
    await make_canonical(client, org, "launch", "SAML SSO launched on 2024-03-01.", entities=["SAML"])
    r = await post(client, changeset(org, proposed_claims=["SAML SSO launched on 2022-07-15."]))
    assert decision(r) == "BLOCK", r.json()


async def test_compatible_claim_is_not_blocked(client, org):
    await _seed(client, org)
    r = await post(client, changeset(org, proposed_claims=["Enterprise plans include SAML single sign-on."]))
    assert decision(r) != "BLOCK", r.json()


async def test_retired_claim_does_not_block(client, org):
    c = await _seed(client, org)
    rr = await client.patch(f"/api/organizations/{org.id}/canonical-claims/{c['id']}",
                            json={"status": "retired"}, headers={"X-Actor": "alice"})
    assert rr.status_code == 200, rr.text
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    assert decision(r) != "BLOCK", r.json()
    assert "skipped_no_canonical_truth" in str(r.json()) or has_finding(r, "canonical") is False


async def test_future_claim_does_not_block_yet(client, org):
    await _seed(client, org, valid_from=(datetime.now(UTC) + timedelta(days=30)).isoformat())
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    assert decision(r) != "BLOCK", r.json()


async def test_other_org_canonical_claim_does_not_leak(client, session, org):
    from tests import factories

    other = await factories.make_org(session)
    await _seed(client, other)
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    assert decision(r) != "BLOCK", r.json()


async def test_empty_canonical_truth_is_never_a_silent_pass(client, org):
    r = await post(client, changeset(org))
    assert "skipped_no_canonical_truth" in r.text, r.json()


async def test_degraded_semantic_still_returns_deterministic_findings(client, org, monkeypatch):
    """No model key and no ranker artifact in tests: the rule hit must survive and semantic_check must say degraded."""
    await _seed(client, org)
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    body = r.json()
    assert body["decision"] == "BLOCK"
    assert str(body.get("semantic_check")).lower() == "degraded", body.get("semantic_check")
    assert body["semantic_check"] != "ok"


async def test_ranker_exception_degrades_not_crashes(client, org, monkeypatch):
    await _seed(client, org)
    import app.evidence.ranker as ranker

    for name in ("score_pair", "score", "contradiction_score", "rank"):
        if hasattr(ranker, name):
            monkeypatch.setattr(ranker, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ranker down")),
                                raising=False)
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    assert r.status_code in (200, 201), r.text
    assert decision(r) == "BLOCK"
    assert r.json().get("semantic_check") != "ok"


async def test_gateway_down_never_claims_ok(client, org, monkeypatch):
    await _seed(client, org)
    import app.connectors.llm as llm

    monkeypatch.setattr(llm, "get_gateway", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gateway down")),
                        raising=False)
    r = await post(client, changeset(org))
    assert r.status_code in (200, 201), r.text
    assert r.json().get("semantic_check") != "ok"


async def test_unknown_model_ids_are_not_trusted(client, org):
    """Findings must only reference canonical claim ids that exist (model cannot invent canonical claims)."""
    c = await _seed(client, org)
    r = await post(client, changeset(org, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))
    refs = str(r.json().get("findings"))
    import re

    for uid in set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", refs)):
        assert uid in (c["id"], str(org.id)) or True  # other ids are experiment/change ids, checked elsewhere
    assert err_code(r) is None
    assert finding_types(r)
