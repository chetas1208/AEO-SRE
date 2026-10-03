"""Profound connection health, derived from capability reasons. Provider-shaped reasons stay in this package."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class ProfoundHealth(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONNECTED = "CONNECTED"
    AUTH_FAILED = "AUTH_FAILED"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"


# Surface reasons that describe the ACCOUNT's configuration, not the connection: the API answered correctly, the
# customer simply tracks no competitor assets. The surface itself stays `degraded` with this reason (nothing is
# hidden or fabricated); it just must not drag the whole connector to DEGRADED.
ACCOUNT_CONFIG_REASONS = frozenset({"no_competitor_assets_tracked"})


def counts_as_healthy(surface: Mapping[str, Any]) -> bool:
    return surface.get("state") == "healthy" or (
        surface.get("state") == "degraded" and surface.get("reason") in ACCOUNT_CONFIG_REASONS
    )


def derive_health(configured: bool, surfaces: Mapping[str, Mapping[str, Any]] | None) -> ProfoundHealth:
    """Worst-first: AUTH_FAILED > RATE_LIMITED > DEGRADED > CONNECTED. `agents` is optional and ignored.

    Surfaces degraded only by ACCOUNT_CONFIG_REASONS count as healthy for the connector verdict. CONNECTED requires at least one live-verified healthy core surface; with no probe result the connector is
    DEGRADED (configured but unproven), never CONNECTED.
    """
    if not configured:
        return ProfoundHealth.NOT_CONFIGURED
    core = {k: v for k, v in (surfaces or {}).items() if k != "agents"}
    if not core:
        return ProfoundHealth.DEGRADED
    reasons = " ".join(str(v.get("reason") or "") for v in core.values())
    if "not_configured" in reasons:
        return ProfoundHealth.NOT_CONFIGURED
    if "auth_failed" in reasons:
        return ProfoundHealth.AUTH_FAILED
    if "rate_limited" in reasons:
        return ProfoundHealth.RATE_LIMITED
    healthy = [v for v in core.values() if counts_as_healthy(v)]
    if len(healthy) == len(core):
        return ProfoundHealth.CONNECTED
    return ProfoundHealth.DEGRADED
