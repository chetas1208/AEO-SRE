"""Typed Profound errors. Messages never contain credentials."""

from __future__ import annotations


class ProfoundError(Exception):
    """Base class. `status` is the HTTP status when the error came from a response."""

    def __init__(self, message: str, *, status: int | None = None, endpoint: str | None = None, detail: object = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.endpoint = endpoint
        self.detail = detail

    def __str__(self) -> str:
        bits = [self.message]
        if self.endpoint:
            bits.append(f"[{self.endpoint}]")
        if self.status:
            bits.append(f"(HTTP {self.status})")
        return " ".join(bits)


class ProfoundNotConfigured(ProfoundError):
    """No API key configured."""


class ProfoundAuthError(ProfoundError):
    """401: missing, invalid or expired key."""


class ProfoundPermissionError(ProfoundError):
    """403: key lacks access to the resource (e.g. category, FactCheck not enabled)."""


class ProfoundNotFound(ProfoundError):
    """404."""


class ProfoundValidationError(ProfoundError):
    """400/422: request rejected. `detail` carries the server's validation payload."""


class ProfoundRateLimited(ProfoundError):
    """429 after retries were exhausted (or Retry-After exceeded the allowed wait)."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ProfoundServerError(ProfoundError):
    """5xx after retries."""


class ProfoundTransportError(ProfoundError):
    """Network failure or timeout after retries."""


class ProfoundResponseError(ProfoundError):
    """2xx but the body is not the documented shape (not JSON, missing `data`, ...)."""
