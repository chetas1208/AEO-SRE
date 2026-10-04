"""When Profound is configured, the product surface is LIVE-only (no demo fixtures)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.core.config import get_settings
from app.services.profound_agents import profound_generation_enabled

# Narrative demo campaigns (financial fiction). Hidden whenever PROFOUND_API_KEY is set.
BUILT_IN_CAMPAIGN_IDS = frozenset(
    {
        "cmp-ai-discovery-launch-01",
        "cmp-sso-parity-q4",
        "cmp-developer-experience-01",
        "cmp-cloud-security-rebrand",
        "cmp-saml-expansion-q3",
    }
)


def live_surface_enabled() -> bool:
    """True → API/UI prefer Postgres + Profound + real agent runs; omit built-in fixtures."""
    if profound_generation_enabled():
        return True
    return get_settings().environment != "test" and bool(get_settings().profound_api_key)


def iter_public_campaigns(campaigns_db: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for c in campaigns_db:
        if live_surface_enabled() and c.get("id") in BUILT_IN_CAMPAIGN_IDS:
            continue
        yield c
