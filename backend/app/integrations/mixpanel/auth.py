"""Mixpanel service-account credentials (never logged)."""

from __future__ import annotations

import base64

from app.core.config import Settings


def mixpanel_configured(settings: Settings) -> bool:
    return bool(
        settings.mixpanel_enabled
        and settings.mixpanel_project_id
        and settings.mixpanel_service_account_username
        and settings.mixpanel_service_account_secret
    )


def basic_auth_header(settings: Settings) -> str:
    user = settings.mixpanel_service_account_username
    secret = settings.mixpanel_service_account_secret
    token = base64.b64encode(f"{user}:{secret}".encode()).decode("ascii")
    return f"Basic {token}"


def api_host(settings: Settings) -> str:
    explicit = (settings.mixpanel_api_host or "").strip().rstrip("/")
    if explicit:
        return explicit
    region = (settings.mixpanel_region or "US").upper()
    if region in ("EU", "EUROPE"):
        return "https://eu.mixpanel.com"
    return "https://mixpanel.com"
