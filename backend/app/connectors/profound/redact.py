"""Redact secrets from Profound diagnostics before logging or CLI output."""

from __future__ import annotations

import re
from typing import Any

_REDACT_KEYS = frozenset(
    {
        "authorization",
        "x-api-key",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)
_BEARER = re.compile(r"Bearer\s+\S+", re.I)
_KEYish = re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*\S+")


def _redact_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if _BEARER.search(value) or _KEYish.search(value):
        return "[REDACTED]"
    return value


def redact_for_display(value: Any) -> Any:
    """Deep-copy structures with sensitive keys and header-like strings removed."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).lower().replace("_", "-") in _REDACT_KEYS or str(k).lower() in _REDACT_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_for_display(v)
        return out
    if isinstance(value, list):
        return [redact_for_display(v) for v in value]
    return _redact_scalar(value)
