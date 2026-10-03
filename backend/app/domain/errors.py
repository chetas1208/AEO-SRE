"""Domain errors and their HTTP contract.

Every error carries a stable machine `code`, the HTTP status the API maps it to (centrally, in
`app.api.errors`), and optional JSON-safe `details`. No stack trace or secret is ever part of an error body.
Domain code raises these; only the API layer turns them into responses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


class DomainError(Exception):
    code = "DOMAIN_ERROR"
    http_status = 400
    error_type = "bad_request"  # legacy lowercase alias kept for existing clients

    def __init__(self, message: str, details: dict[str, Any] | None = None, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code


class InvalidStateTransition(DomainError):
    code, http_status, error_type = "INVALID_STATE_TRANSITION", 409, "illegal_transition"


class InsufficientEvidence(DomainError):
    code, http_status, error_type = "INSUFFICIENT_EVIDENCE", 409, "conflict"


class ExperimentNotVerifiable(DomainError):
    code, http_status, error_type = "EXPERIMENT_NOT_VERIFIABLE", 409, "conflict"

    @classmethod
    def too_early(cls, eligible_at: datetime) -> ExperimentNotVerifiable:
        iso = eligible_at.isoformat().replace("+00:00", "Z")
        return cls(
            f"verification window opens {iso}; no outcome can be measured before then",
            {"eligible_at": iso}, code="EXPERIMENT_NOT_VERIFIABLE_YET",
        )


class ProviderNotConfigured(DomainError):
    code, http_status, error_type = "PROVIDER_NOT_CONFIGURED", 503, "unavailable"


class ProviderUnavailable(DomainError):
    code, http_status, error_type = "PROVIDER_UNAVAILABLE", 503, "unavailable"


class InvalidEvidenceReference(DomainError):
    code, http_status, error_type = "INVALID_EVIDENCE_REFERENCE", 422, "validation_error"


class ActionNotEligible(DomainError):
    code, http_status, error_type = "ACTION_NOT_ELIGIBLE", 409, "conflict"


class DuplicateReward(DomainError):
    code, http_status, error_type = "DUPLICATE_REWARD", 409, "conflict"


# --- Change Guard -----------------------------------------------------------------------------------------------
class ChangeCheckKeyConflict(DomainError):
    """Same idempotency key, different proposal digest."""

    code, http_status, error_type = "CHANGE_CHECK_KEY_CONFLICT", 409, "conflict"


class ApprovalDigestMismatch(DomainError):
    """The change differs from what the human approved: the approval no longer applies."""

    code, http_status, error_type = "APPROVAL_DIGEST_MISMATCH", 409, "conflict"


class ChangeGuardBlocked(DomainError):
    """BLOCK / DELAY (or REQUIRE_REVIEW without a review_reason): approval refused; findings in `details`."""

    code, http_status, error_type = "CHANGE_GUARD_BLOCKED", 409, "conflict"


class CanonicalClaimInvalid(DomainError):
    code, http_status, error_type = "CANONICAL_CLAIM_INVALID", 422, "validation_error"


class ChangeGuardNotConfigured(DomainError):
    code, http_status, error_type = "CHANGE_GUARD_NOT_CONFIGURED", 503, "unavailable"


class ChangeGuardUnauthorized(DomainError):
    code, http_status, error_type = "CHANGE_GUARD_UNAUTHORIZED", 401, "unauthorized"


class RateLimited(DomainError):
    code, http_status, error_type = "RATE_LIMITED", 429, "rate_limited"


class OrganizationNotFound(DomainError):
    code, http_status, error_type = "NOT_FOUND", 404, "not_found"


class ChangeSetInvalid(DomainError):
    code, http_status, error_type = "CHANGE_SET_INVALID", 422, "validation_error"


# ---- graph API (N6): typed errors for /api/graph/*. Read views normally DEGRADE (200, source=unavailable) instead
# of raising; these cover the paths that cannot degrade (org resolution, parameters, unexpected graph failures).
class GraphUnavailableError(DomainError):
    code, http_status, error_type = "GRAPH_UNAVAILABLE", 503, "unavailable"


class GraphQueryFailed(DomainError):
    code, http_status, error_type = "GRAPH_QUERY_FAILED", 503, "unavailable"


class GraphOrgRequired(DomainError):
    code, http_status, error_type = "GRAPH_ORG_REQUIRED", 422, "validation_error"


class GraphInvalidParameter(DomainError):
    code, http_status, error_type = "GRAPH_INVALID_PARAMETER", 422, "validation_error"
