"""Smallest safe live Profound check. Run once PROFOUND_API_KEY is set (the only cutover step besides a restart).

    cd backend && .venv/bin/python -m app.devtools.profound_smoke [--minimal]

1. validate configuration (key present, https base URL) without printing the key;
2. one read-only request (`GET /v1/org/categories`), normalized by the connector;
3. unless --minimal: the capability probe (one row-limited read per surface; no writes, no agent runs);
4. print the redacted report and the health state (NOT_CONFIGURED | CONNECTED | AUTH_FAILED | DEGRADED | RATE_LIMITED).

Exit codes: 0 connected, 1 degraded, 2 not configured, 3 auth failed, 4 rate limited.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from urllib.parse import urlsplit

from app.connectors.profound import ProfoundClient, ProfoundError, normalize_categories
from app.connectors.profound.capabilities import _pick_probe_category, probe_capabilities, status_from_error
from app.connectors.profound.health import ProfoundHealth, derive_health
from app.connectors.profound.redact import redact_for_display

EXIT = {
    ProfoundHealth.CONNECTED: 0,
    ProfoundHealth.DEGRADED: 1,
    ProfoundHealth.NOT_CONFIGURED: 2,
    ProfoundHealth.AUTH_FAILED: 3,
    ProfoundHealth.RATE_LIMITED: 4,
}


def validate_config(client: ProfoundClient) -> list[str]:
    problems: list[str] = []
    if not client.configured:
        problems.append("PROFOUND_API_KEY is not set")
    parts = urlsplit(client.base_url)
    if parts.scheme != "https" or not parts.hostname:
        problems.append("base URL must be https with a host")
    return problems


async def smoke(client: ProfoundClient | None = None, *, minimal: bool = False) -> tuple[dict[str, Any], int]:
    owns = client is None
    client = client or ProfoundClient()
    report: dict[str, Any] = {
        "provider": "profound", "source_mode": "LIVE", "base_url": client.base_url,
        "key_configured": client.configured, "non_destructive": True,
        "key_hint": None,  # never print any part of the key
    }
    try:
        problems = validate_config(client)
        if not client.configured:
            report.update(health=ProfoundHealth.NOT_CONFIGURED.value, problems=problems,
                          next="set PROFOUND_API_KEY in the root .env and restart; nothing else changes")
            return report, EXIT[ProfoundHealth.NOT_CONFIGURED]
        if problems:
            report.update(health=ProfoundHealth.DEGRADED.value, problems=problems)
            return report, EXIT[ProfoundHealth.DEGRADED]
        # 2. smallest safe request, normalized
        try:
            resp = await client.list_categories()
        except ProfoundError as e:
            state, reason = status_from_error(e)
            surfaces = {"categories": {"state": state.value, "reason": reason}}
            health = derive_health(True, surfaces)
            report.update(health=health.value, request={"endpoint": "GET /v1/org/categories", "ok": False,
                                                        "reason": reason}, error=str(e))
            return report, EXIT[health]
        cats = normalize_categories(resp.data)
        report["request"] = {
            "endpoint": "GET /v1/org/categories", "ok": True, "http_status": resp.status,
            "normalized_categories": len(cats), "first_category": cats[0].name if cats else None,
            "rate_limit": resp.rate_limit,
        }
        surfaces: dict[str, dict[str, Any]] = {}
        if not minimal and cats:
            probe_cat = await _pick_probe_category(client, resp.data if isinstance(resp.data, list) else [])
            report["probe_category"] = probe_cat.get("name")
            caps = await probe_capabilities(client, category_id=str(probe_cat.get("id")))
            surfaces = {name: st.to_dict() for name, st in caps.items()}
            report["capabilities"] = surfaces
            report["healthy_surfaces"] = [k for k, v in surfaces.items() if v["state"] == "healthy"]
        elif not cats:
            surfaces = {"categories": {"state": "degraded", "reason": "no_category_accessible"}}
            report["capabilities"] = surfaces
        else:
            surfaces = {"categories": {"state": "healthy", "reason": None}}
        health = derive_health(True, surfaces)
        report["health"] = health.value
        return report, EXIT[health]
    finally:
        if owns:
            await client.aclose()


def main() -> None:
    minimal = "--minimal" in sys.argv[1:]
    report, code = asyncio.run(smoke(minimal=minimal))
    print(json.dumps(redact_for_display(report), indent=2, default=str))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
