"""Read-only Mixpanel connectivity smoke. Run: make mixpanel-smoke"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.config import get_settings
from app.integrations.mixpanel.auth import mixpanel_configured
from app.integrations.mixpanel.client import MixpanelClient
from app.integrations.mixpanel.health import MixpanelHealth, derive_health

EXIT = {
    MixpanelHealth.READY: 0,
    MixpanelHealth.DEGRADED: 1,
    MixpanelHealth.NOT_CONFIGURED: 2,
    MixpanelHealth.AUTH_FAILED: 3,
    MixpanelHealth.RATE_LIMITED: 4,
}


async def smoke() -> tuple[dict[str, Any], int]:
    settings = get_settings()
    report: dict[str, Any] = {
        "provider": "mixpanel",
        "read_only": True,
        "project_id": settings.mixpanel_project_id or None,
        "region": settings.mixpanel_region,
        "configured": mixpanel_configured(settings),
    }
    if not report["configured"]:
        state, meta = derive_health(configured=False, auth_ok=None, rate_limited=False, last_sync_at=None, lag_seconds=None)
        report["health"] = state
        report.update(meta)
        return report, EXIT[state]

    client = MixpanelClient(settings)
    ok, latency_ms, err = await client.ping_auth()
    sample_meta: dict[str, Any] = {}
    if ok:
        sample = await client.sample_events(limit=5)
        sample_meta = {
            "sample_count": len(sample),
            "sample_event_names": sorted({str(r.get("event")) for r in sample if r.get("event")})[:10],
        }
    state, meta = derive_health(
        configured=True,
        auth_ok=ok,
        rate_limited=err == "rate limited",
        last_sync_at=None,
        lag_seconds=None,
        error=err,
    )
    report["health"] = state
    report["latency_ms"] = latency_ms
    report.update(meta)
    report.update(sample_meta)
    return report, EXIT[state]


def main() -> None:
    report, code = asyncio.run(smoke())
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
