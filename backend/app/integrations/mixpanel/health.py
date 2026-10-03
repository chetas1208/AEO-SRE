"""Mixpanel integration health states for /api/system/capabilities."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class MixpanelHealth(StrEnum):
    READY = "READY"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTH_FAILED = "AUTH_FAILED"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"


def derive_health(*, configured: bool, auth_ok: bool | None, rate_limited: bool, last_sync_at: str | None,
                  lag_seconds: float | None, error: str | None = None) -> tuple[MixpanelHealth, dict[str, Any]]:
    meta: dict[str, Any] = {
        "last_sync_at": last_sync_at,
        "lag_seconds": lag_seconds,
        "sync_mode": "near_real_time_polling",
    }
    if not configured:
        return MixpanelHealth.NOT_CONFIGURED, {**meta, "detail": "Mixpanel credentials not configured"}
    if rate_limited:
        return MixpanelHealth.RATE_LIMITED, {**meta, "detail": "Mixpanel rate limit"}
    if auth_ok is False:
        return MixpanelHealth.AUTH_FAILED, {**meta, "detail": error or "authentication failed"}
    if error:
        return MixpanelHealth.DEGRADED, {**meta, "detail": error}
    if last_sync_at is None:
        return MixpanelHealth.DEGRADED, {**meta, "detail": "never synced successfully"}
    return MixpanelHealth.READY, meta
