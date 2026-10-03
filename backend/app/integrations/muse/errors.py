"""Typed connector errors: {"error": {"code", "type", "message", "details", "request_id"}}."""
from __future__ import annotations

from typing import Any


class MuseError(Exception):
    code = "MUSE_ERROR"
    http_status = 400
    error_type = "bad_request"

    def __init__(self, message: str, details: Any = None, *, headers: dict[str, str] | None = None,
                 code: str | None = None) -> None:
        super().__init__(message)
        self.message, self.details, self.headers = message, details, headers or {}
        if code:
            self.code = code

    def body(self, request_id: str | None) -> dict[str, Any]:
        return {"error": {"code": self.code, "type": self.error_type, "message": self.message,
                          "details": self.details, "request_id": request_id}}


class NotConfigured(MuseError):
    code, http_status, error_type = "MUSE_CONNECTOR_NOT_CONFIGURED", 503, "unavailable"


class OrganizationNotConfigured(MuseError):
    code, http_status, error_type = "MUSE_ORGANIZATION_NOT_CONFIGURED", 503, "unavailable"


class Unauthorized(MuseError):
    code, http_status, error_type = "MUSE_UNAUTHORIZED", 401, "unauthorized"


class RateLimited(MuseError):
    code, http_status, error_type = "RATE_LIMITED", 429, "rate_limited"


class PayloadTooLarge(MuseError):
    code, http_status, error_type = "PAYLOAD_TOO_LARGE", 413, "payload_too_large"


class InvalidRequest(MuseError):
    code, http_status, error_type = "INVALID_REQUEST", 422, "validation_error"


class InvalidIntentEnvelope(MuseError):
    code, http_status, error_type = "INVALID_INTENT_ENVELOPE", 422, "validation_error"


class IdentityFieldRejected(MuseError):
    code, http_status, error_type = "IDENTITY_FIELD_REJECTED", 422, "validation_error"


class IntentExpired(MuseError):
    code, http_status, error_type = "INTENT_EXPIRED", 422, "validation_error"


class NotFound(MuseError):
    code, http_status, error_type = "NOT_FOUND", 404, "not_found"


class Conflict(MuseError):
    code, http_status, error_type = "IDEMPOTENCY_KEY_CONFLICT", 409, "conflict"


class ToolTimeout(MuseError):
    code, http_status, error_type = "TOOL_TIMEOUT", 504, "timeout"


class Unavailable(MuseError):
    code, http_status, error_type = "DEPENDENCY_UNAVAILABLE", 503, "unavailable"


class Internal(MuseError):
    code, http_status, error_type = "INTERNAL_ERROR", 500, "internal_error"


ERROR_CODES: dict[str, tuple[int, str]] = {c.code: (c.http_status, c.__doc__ or "") for c in (
    NotConfigured, OrganizationNotConfigured, Unauthorized, RateLimited, PayloadTooLarge, InvalidRequest,
    InvalidIntentEnvelope, IdentityFieldRejected, IntentExpired, NotFound, Conflict, ToolTimeout, Unavailable, Internal)}
ERROR_DESCRIPTIONS = {
    "MUSE_CONNECTOR_NOT_CONFIGURED": "the connector key is not set on the server",
    "MUSE_ORGANIZATION_NOT_CONFIGURED": "the connector key is not bound to an organization",
    "MUSE_UNAUTHORIZED": "missing or invalid bearer key",
    "RATE_LIMITED": "per-key rate limit exceeded; see Retry-After and details.retry_after_seconds",
    "PAYLOAD_TOO_LARGE": "request body exceeds the connector limit",
    "INVALID_REQUEST": "the body does not match the tool input schema",
    "INVALID_INTENT_ENVELOPE": "the IntentEnvelope does not match its schema or limits",
    "IDENTITY_FIELD_REJECTED": "the envelope carries an identity/PII-looking key or value; it is never accepted",
    "INTENT_EXPIRED": "the envelope's expires_at is in the past or its ttl is not positive",
    "NOT_FOUND": "the referenced record does not exist for the bound organization",
    "IDEMPOTENCY_KEY_CONFLICT": "the idempotency_key was already used with a different payload",
    "TOOL_TIMEOUT": "the tool exceeded the request time budget",
    "DEPENDENCY_UNAVAILABLE": "the database is unavailable; retry shortly",
    "INTERNAL_ERROR": "unexpected error (details only in server logs)",
}
