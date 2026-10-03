"""IntentEnvelope: the provider-agnostic form of a Muse request. No identity, no personal profile, bounded size.

Everything downstream of the adapter consumes this (or plain strings), never a Muse wire type.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.experiments import window as vwindow

MAX_TTL_SECONDS = 24 * 3600
MAX_CONSTRAINTS = 20
MAX_PREFERENCES = 20
MAX_REQUIREMENTS = 10
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")

# Tokens (split on _ - . and camelCase) that make a key identity-like on their own.
_IDENTITY_TOKENS = {
    "email", "emails", "phone", "telephone", "mobile", "msisdn", "address", "street", "zip", "zipcode", "postcode",
    "ssn", "dob", "birthday", "birthdate", "passport", "username", "login", "userid", "uid", "deviceid", "imei",
    "idfa", "gaid", "ip", "ipaddress", "fingerprint", "latitude", "longitude", "gps", "geolocation", "cookie",
    "session", "sessionid", "accountid", "customerid", "memberid", "handle", "avatar", "photo",
}
_NAME_QUALIFIERS = {"first", "last", "full", "user", "display", "given", "family", "real", "middle", "sur",
                    "nick", "person", "customer", "account", "contact", "owner"}
_ID_PARTNERS = {"user", "device", "account", "customer", "member", "person", "profile", "session", "contact"}


def _tokens(key: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    return [t for t in re.split(r"[\s_.\-]+", spaced.lower()) if t]


def identity_reason(key: str) -> str | None:
    """Why `key` looks like an identity/PII field, or None."""
    toks = _tokens(key)
    joined = "".join(toks)
    if joined in _IDENTITY_TOKENS or any(t in _IDENTITY_TOKENS for t in toks):
        return "identity-like key"
    if "name" in toks and (len(toks) == 1 or any(t in _NAME_QUALIFIERS for t in toks)):
        return "personal name key"
    if "id" in toks and any(t in _ID_PARTNERS for t in toks):
        return "identifier key"
    return None


def _reject(kind: str, msg: str) -> PydanticCustomError:
    return PydanticCustomError(kind, msg)


def _check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise _reject("invalid_key", "keys must be 1-64 chars of letters, digits, _ . - and start with a letter")
    why = identity_reason(key)
    if why:
        raise _reject("identity_field_rejected",
                      f"key {key!r} is rejected ({why}): the envelope must not contain identity or personal data")
    return key


def _check_text(v: str) -> str:
    if EMAIL_RE.search(v):
        raise _reject("identity_field_rejected",
                      "a value looks like an email address: the envelope must not contain identity or personal data")
    return v


Scalar = str | int | float | bool


class Range(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float | None = None
    max: float | None = None
    unit: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if self.min is None and self.max is None:
            raise _reject("invalid_range", "a range needs min and/or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise _reject("invalid_range", "range min must be <= max")
        return self


ConstraintValue = Scalar | Range


class IntentEnvelope(BaseModel):
    """What the user is trying to do, stripped to the task: intent, constraints, task-relevant preferences, expiry."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "intent": "compare enterprise SSO support",
        "constraints": {"plan": "enterprise", "price_usd": {"max": 50}},
        "requirements": ["SAML SSO is available on the Enterprise plan."],
        "preferences": {"audience": "it_admin"}, "ttl_seconds": 600, "source": "muse"}]})

    intent: str = Field(min_length=1, max_length=1000)
    constraints: dict[str, ConstraintValue] = Field(default_factory=dict)
    requirements: list[str] = Field(default_factory=list, max_length=MAX_REQUIREMENTS,
                                    description="plain-language statements the task needs to be true")
    preferences: dict[str, Scalar] = Field(default_factory=dict)
    expires_at: datetime | None = None
    ttl_seconds: int | None = Field(default=None, description="max 86400 (24h); counted from receipt")
    source: Literal["muse"] = "muse"

    @field_validator("intent")
    @classmethod
    def _intent(cls, v: str) -> str:
        return _check_text(v.strip())

    @field_validator("requirements")
    @classmethod
    def _reqs(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            s = s.strip()
            if not s or len(s) > 300:
                raise _reject("invalid_requirement", "each requirement must be 1-300 characters")
            out.append(_check_text(s))
        return out

    @field_validator("constraints", "preferences")
    @classmethod
    def _maps(cls, v: dict[str, Any], info) -> dict[str, Any]:
        limit = MAX_CONSTRAINTS if info.field_name == "constraints" else MAX_PREFERENCES
        if len(v) > limit:
            raise _reject("too_many_entries", f"at most {limit} {info.field_name}")
        for k, val in v.items():
            _check_key(k)
            if isinstance(val, str):
                if len(val) > 200:
                    raise _reject("value_too_long", f"value of {k!r} exceeds 200 characters")
                _check_text(val)
        return v

    @field_validator("ttl_seconds")
    @classmethod
    def _ttl(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v <= 0:
            raise _reject("intent_expired", "ttl_seconds must be positive")
        if v > MAX_TTL_SECONDS:
            raise _reject("intent_ttl_too_long", f"ttl_seconds must be at most {MAX_TTL_SECONDS} (24h)")
        return v

    @model_validator(mode="after")
    def _expiry(self) -> IntentEnvelope:
        if self.expires_at is not None:
            exp = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
            now = vwindow.now()
            if exp <= now:
                raise _reject("intent_expired", "expires_at is in the past: the intent has expired")
            if exp > now + timedelta(seconds=MAX_TTL_SECONDS):
                raise _reject("intent_ttl_too_long", "expires_at is more than 24h ahead")
        if not (self.constraints or self.requirements):
            raise _reject("nothing_to_check", "provide at least one constraint or requirement")
        return self

    def statements(self) -> list[tuple[str, str]]:
        """(label, plain-language statement) for every constraint and requirement, in order."""
        out: list[tuple[str, str]] = []
        for k, v in self.constraints.items():
            out.append((f"constraint:{k}", render_constraint(k, v)))
        for i, r in enumerate(self.requirements):
            out.append((f"requirement:{i}", r))
        return out


def render_constraint(key: str, value: ConstraintValue) -> str:
    name = key.replace("_", " ").replace(".", " ").replace("-", " ")
    if isinstance(value, Range):
        unit = f" {value.unit}" if value.unit else ""
        if value.min is not None and value.max is not None:
            return f"{name} is between {value.min:g} and {value.max:g}{unit}"
        if value.max is not None:
            return f"{name} is at most {value.max:g}{unit}"
        return f"{name} is at least {value.min:g}{unit}"
    if isinstance(value, bool):
        return f"{name} is {'available' if value else 'not available'}"
    return f"{name} is {value}"
