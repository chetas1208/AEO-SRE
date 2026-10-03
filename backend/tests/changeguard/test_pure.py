"""Digest, URL normalization, overlap and precedence: pure functions."""
from __future__ import annotations

import pytest
from app.changeguard import digest as dg
from app.changeguard.decision import Decision, Finding, decide, latest_eligible_after
from app.changeguard.overlap import ClusterInfo, cluster_overlap, cluster_prompts, target_overlap
from app.changeguard.targets import match_kind, normalize_url
from app.experiments.window import aware


@pytest.mark.parametrize("raw,expected", [
    ("HTTPS://TestCo.Example/Docs/", "https://testco.example/docs"),
    ("testco.example/docs", "https://testco.example/docs"),
    ("https://testco.example:443/docs#frag", "https://testco.example/docs"),
    ("http://testco.example:80/docs", "http://testco.example/docs"),
    ("https://testco.example:8443/docs", "https://testco.example:8443/docs"),
    ("https://testco.example/docs?utm_source=x&b=2&a=1&gclid=zz", "https://testco.example/docs?a=1&b=2"),
    ("https://testco.example//docs///page/", "https://testco.example/docs/page"),
    ("https://testco.example/", "https://testco.example"),
    ("http://www.testco.example/enterprise/security", "http://testco.example/enterprise/security"),
    ("https://testco.example/enterprise/%73ecurity", "https://testco.example/enterprise/security"),
    ("https://testco.example/enterprise/./security", "https://testco.example/enterprise/security"),
    ("https://testco.example/a/../enterprise/security", "https://testco.example/enterprise/security"),
    ("https://TESTCO.EXAMPLE/ENTERPRISE/SECURITY", "https://testco.example/enterprise/security"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://testco.example/x", "https://", "not a url", "https://nodot"])
def test_normalize_url_rejects_garbage(raw):
    with pytest.raises(ValueError):
        normalize_url(raw)


def test_match_kind_exact_prefix_none():
    base = "https://testco.example/enterprise"
    assert match_kind(normalize_url(base), normalize_url("HTTPS://testco.example/enterprise/#x")) == "exact"
    assert match_kind(base, "https://testco.example/enterprise/security") == "prefix"
    assert match_kind("https://testco.example/enterprise/security", base) == "prefix"
    assert match_kind(base, "https://testco.example/enterprises") is None  # segment boundary, not string prefix
    assert match_kind(base, "https://other.example/enterprise") is None
    # the bare site root is only an exact match, never a prefix of the whole site
    assert match_kind("https://testco.example", "https://testco.example/enterprise") is None
    assert match_kind("https://testco.example", "https://testco.example") == "exact"
    # scheme-insensitive
    assert match_kind("http://testco.example/a", "https://testco.example/a") == "exact"


def test_target_overlap_percent_is_share_of_protected_pages():
    protected = ["https://t.example/a", "https://t.example/b", "https://t.example/c/d", "https://t.example/e"]
    assert target_overlap("https://t.example/a", protected).pct == 25.0
    assert target_overlap("https://t.example/c", protected).pct == 25.0  # section covers /c/d
    assert target_overlap("https://t.example/zzz", protected).pct == 0.0
    assert target_overlap(None, protected).pct == 0.0
    assert target_overlap("https://t.example/a", []).pct == 0.0


def test_cluster_overlap_bases():
    c = ClusterInfo("c1", "Enterprise SSO", cluster_prompts(["best sso tool", {"text": "does it support SAML"}]))
    assert cluster_overlap(c, change_cluster_ids=["c1"], change_prompts=[], text_blob="").basis == "cluster_id"
    r = cluster_overlap(c, change_cluster_ids=[], change_prompts=["Best  SSO tool"], text_blob="")
    assert (r.pct, r.basis) == (50.0, "prompts")
    r = cluster_overlap(c, change_cluster_ids=[], change_prompts=[], text_blob="we improve enterprise sso docs")
    assert (r.pct, r.basis) == (100.0, "topic_mention")
    assert cluster_overlap(c, change_cluster_ids=[], change_prompts=[], text_blob="pricing page").pct == 0.0
    assert cluster_overlap(None, change_cluster_ids=["c1"], change_prompts=[], text_blob="").pct == 0.0


def test_claim_normalization_is_order_case_and_whitespace_insensitive():
    a = dg.normalize_claims(["SAML  SSO is available.", "Price is $10 "])
    b = dg.normalize_claims(["price is $10", "saml sso is available"])
    assert a == b == ["price is $10", "saml sso is available"]
    assert dg.normalize_claims(["", "  ", None]) == []


def _p(**kw):
    base = dict(org_id="o1", agent_id="a", target_key="https://t.example/x", action_type="update_existing_page",
                claims=["a claim"], content_hash=None)
    base.update(kw)
    return dg.proposal_dict(**base)


def test_digest_changes_with_every_proposal_field_and_context_but_not_claim_order():
    base = dg.proposal_digest(_p())
    assert base == dg.proposal_digest(_p())
    for change in (dict(org_id="o2"), dict(agent_id="b"), dict(target_key="https://t.example/y"),
                   dict(action_type="create_faq"), dict(claims=["other"]), dict(content_hash="abc")):
        assert dg.proposal_digest(_p(**change)) != base, change
    assert dg.proposal_digest(_p(claims=dg.normalize_claims(["b", "a"]))) == dg.proposal_digest(
        _p(claims=dg.normalize_claims(["a", "b"])))
    ctx = [{"experiment_id": "e1", "status": "executed"}]
    d1 = dg.action_digest(_p(), ctx)
    assert d1 != dg.action_digest(_p(), []) and d1 != dg.action_digest(_p(), [{"experiment_id": "e1", "status": "awaiting_verification"}])
    assert dg.action_digest(_p(), ctx) == dg.action_digest(_p(), list(ctx))
    assert dg.text_hash(None, None) is None and dg.text_hash("a\r\nb", None) == dg.text_hash("a\nb", None)
    assert dg.text_hash("a", None) != dg.text_hash("a", "diff")


def test_precedence_block_over_delay_over_review_over_merge_over_allow():
    order = [Decision.ALLOW, Decision.MERGE, Decision.REQUIRE_REVIEW, Decision.DELAY, Decision.BLOCK]
    for i, hi in enumerate(order):
        for lo in order[: i + 1]:
            fs = [Finding("t", lo, "r"), Finding("t", hi, "r"), Finding("t", Decision.ALLOW, "info")]
            assert decide(fs) == hi
            assert decide(list(reversed(fs))) == hi
    assert decide([]) == Decision.ALLOW
    assert decide([Finding("t", Decision.ALLOW, "info")]) == Decision.ALLOW


def test_latest_eligible_after_only_counts_delay_findings():
    from datetime import UTC, datetime

    a, b = datetime(2026, 10, 4, tzinfo=UTC), datetime(2026, 10, 6, tzinfo=UTC)
    fs = [Finding("t", Decision.DELAY, "r", eligible_after=a), Finding("t", Decision.DELAY, "r", eligible_after=b),
          Finding("t", Decision.MERGE, "r", eligible_after=datetime(2030, 1, 1, tzinfo=UTC))]
    assert aware(latest_eligible_after(fs)) == b
    assert latest_eligible_after([Finding("t", Decision.MERGE, "r")]) is None
