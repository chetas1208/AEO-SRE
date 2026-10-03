from __future__ import annotations

from app.changeguard.claims import Claim, canonical_from_mapping, extract_claims, parse_claim


def test_saml_enterprise_offer():
    c = parse_claim("Acme offers SAML SSO on Enterprise")
    assert (c.subject, c.predicate, c.polarity, c.plans, c.exclusive) == ("saml", "offers", "affirm", ("enterprise",), False)


def test_exclusivity_variants():
    for t in ("SAML is exclusive to Enterprise", "Enterprise-only: SAML", "SAML is available only on Enterprise",
              "Only Enterprise includes SAML"):
        c = parse_claim(t)
        assert c.exclusive and c.plans == ("enterprise",), t


def test_not_only_is_not_exclusive_and_not_negation():
    c = parse_claim("Not only Enterprise but also Business includes SAML")
    assert not c.exclusive and c.polarity == "affirm" and set(c.plans) == {"business", "enterprise"}


def test_not_exclusive_flag():
    c = parse_claim("SAML is not exclusive to Enterprise")
    assert c.not_exclusive and not c.exclusive and c.polarity == "affirm"


def test_negation_and_plans_list():
    assert parse_claim("Acme does not support SAML").polarity == "negate"
    c = parse_claim("Business and Enterprise both include SAML")
    assert c.plans == ("business", "enterprise")
    assert parse_claim("Business and above include SAML").plans == ("business", "enterprise")
    assert parse_claim("All paid plans include SAML").plans == ("starter", "pro", "business", "enterprise")


def test_numbers_and_units():
    c = parse_claim("Free is free up to 10 users")
    assert c.predicate == "limit" and c.subject == "seats" and c.plans == ("free",)
    n = c.numbers[0]
    assert (n.value, n.unit, n.kind) == (10.0, "seats", "max")
    p = parse_claim("The Pro plan costs $29/month per seat")
    assert p.predicate == "price" and p.numbers[0].unit == "usd/seat/month" and p.numbers[0].value == 29.0
    assert parse_claim("Unlimited users on Free").numbers[0].value == float("inf")


def test_dates():
    c = parse_claim("SAML available since Sept 2026")
    assert [(d.role, d.value) for d in c.dates] == [("since", "2026-09")]
    c = parse_claim("Launched on March 3, 2026")
    assert c.dates[0].value == "2026-03-03" and c.dates[0].role == "on"
    assert parse_claim("Okta integration launched in 2026").dates[0].value == "2026"


def test_unknown_wording_has_no_subject():
    c = parse_claim("We love helping customers")
    assert c.subject is None and c.predicate == "other"


def test_text_split_and_spans():
    text = "Business plan includes SAML. SAML is exclusive to Enterprise; Okta is supported."
    cs = extract_claims(text)
    assert len(cs) == 3 and all(c.span for c in cs)
    assert text[cs[0].span[0]:cs[0].span[1]].strip().startswith("Business plan")
    assert len({c.id for c in cs}) == 3


def test_list_input_as_is_and_unique_ids():
    pre = parse_claim("Okta works", claim_id="keep")
    cs = extract_claims(["Enterprise includes SAML", "Enterprise includes SAML", pre, {"id": "x1", "text": "Free up to 5 users"}])
    assert cs[2] is pre
    assert len({c.id for c in cs}) == 4 and cs[3].id == "x1"


def test_deterministic_ids():
    assert extract_claims(["Enterprise includes SAML"])[0].id == extract_claims(["Enterprise includes SAML."])[0].id


def test_canonical_from_mapping():
    c = canonical_from_mapping({"id": "k1", "statement": "Enterprise includes SAML", "status": "retired",
                                "valid_from": "2026-01-01", "scope": "pricing"})
    assert isinstance(c, Claim) and c.status == "retired" and c.valid_from.year == 2026 and c.qualifiers["scope"] == "pricing"
