"""IntentEnvelope validation: no identity, typed errors, expiry, size limits."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.integrations.muse.envelope import IntentEnvelope, identity_reason, render_constraint
from pydantic import ValidationError

from tests import factories as f

pytestmark = pytest.mark.usefixtures("pinned_clock")


def env(**kw):
    base = {"intent": "compare plans", "requirements": ["SSO is offered"]}
    base.update(kw)
    return IntentEnvelope.model_validate(base)


def types(exc: ValidationError) -> set[str]:
    return {e["type"] for e in exc.errors()}


def test_valid_envelope_defaults():
    e = env(constraints={"plan": "enterprise", "price_usd": {"max": 50, "unit": "usd"}, "sso": True},
            preferences={"audience": "it_admin"}, ttl_seconds=60)
    assert e.source == "muse" and [r for r, _ in e.statements()][:3] == ["constraint:plan", "constraint:price_usd", "constraint:sso"]


@pytest.mark.parametrize("key", ["email", "Email", "user_email", "name", "first_name", "fullName", "phone", "phone_number",
                                 "address", "home_address", "user_id", "userId", "device_id", "deviceId", "ip_address",
                                 "username", "ssn", "dob", "session_id", "customer_id", "account_id"])
def test_identity_keys_are_rejected(key):
    assert identity_reason(key)
    for field in ("constraints", "preferences"):
        with pytest.raises(ValidationError) as ei:
            env(**{field: {key: "x"}})
        assert "identity_field_rejected" in types(ei.value)


@pytest.mark.parametrize("key", ["plan_name", "product_name", "budget", "region", "plan", "price_usd", "audience", "id"])
def test_task_keys_are_accepted(key):
    assert identity_reason(key) is None


def test_email_looking_values_rejected_without_echo():
    for kw in ({"intent": "mail me at jo@example.com"}, {"requirements": ["contact jo@example.com"]},
               {"constraints": {"note": "jo@example.com"}}, {"preferences": {"x": "jo@example.com"}}):
        with pytest.raises(ValidationError) as ei:
            env(**kw)
        assert "identity_field_rejected" in types(ei.value)


def test_unknown_fields_and_wrong_source_rejected():
    for kw in ({"user": "bob"}, {"source": "other"}, {"profile": {"age": 3}}):
        with pytest.raises(ValidationError):
            env(**kw)


def test_expiry_rules():
    with pytest.raises(ValidationError) as ei:
        env(expires_at=f.NOW - timedelta(seconds=1))
    assert "intent_expired" in types(ei.value)
    with pytest.raises(ValidationError) as ei:
        env(ttl_seconds=0)
    assert "intent_expired" in types(ei.value)
    with pytest.raises(ValidationError) as ei:
        env(ttl_seconds=86401)
    assert "intent_ttl_too_long" in types(ei.value)
    with pytest.raises(ValidationError) as ei:
        env(expires_at=f.NOW + timedelta(hours=25))
    assert "intent_ttl_too_long" in types(ei.value)
    env(expires_at=f.NOW + timedelta(hours=24), ttl_seconds=86400)


def test_size_limits():
    for kw in ({"intent": "x" * 1001}, {"requirements": ["a b"] * 11}, {"requirements": ["x" * 301]},
               {"constraints": {f"k{i}": 1 for i in range(21)}}, {"constraints": {"k": "x" * 201}},
               {"constraints": {"1bad": 1}}, {"constraints": {"k": {"min": 5, "max": 1}}}, {"constraints": {"k": {}}}):
        with pytest.raises(ValidationError):
            env(**kw)


def test_needs_something_to_check():
    with pytest.raises(ValidationError):
        IntentEnvelope.model_validate({"intent": "hi"})


def test_render_constraint():
    assert render_constraint("sso", True) == "sso is available"
    assert render_constraint("price_usd", env(constraints={"price_usd": {"min": 1, "max": 5}}).constraints["price_usd"]) \
        == "price usd is between 1 and 5"
