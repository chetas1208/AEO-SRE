"""Typed LLM errors. Messages never contain API keys or prompt text."""
from __future__ import annotations


class LLMError(RuntimeError):
    """Base class for every error raised by the LLM connector."""


class LLMUnavailable(LLMError):
    """The model cannot be used right now: no credentials, auth/config error, network failure, retries exhausted."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class LLMInvalidOutput(LLMError):
    """The model answered but the output was not valid JSON / did not match the schema (after one repair retry)."""

    def __init__(self, message: str, *, raw_preview: str = "") -> None:
        super().__init__(message)
        self.raw_preview = raw_preview[:300]


class LLMAuthError(LLMUnavailable):
    """401/403: the key is wrong, revoked or lacks access. Never retried."""


class LLMModelNotFound(LLMUnavailable):
    """The provider does not know MODEL_NAME (404 / 'model not found'). Never retried."""
