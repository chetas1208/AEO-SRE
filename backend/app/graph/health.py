"""Graph health: NOT_CONFIGURED | READY | DEGRADED | AUTH_FAILED. Never raises."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.graph.errors import GraphUnavailable

if TYPE_CHECKING:
    from app.graph.client import GraphClient

STATES = ("NOT_CONFIGURED", "READY", "DEGRADED", "AUTH_FAILED")


async def graph_health(client: GraphClient, *, force: bool = False) -> dict[str, Any]:
    """Live `RETURN 1` probe, cached for `neo4j_health_cache_s` so /api/health stays cheap."""
    s = client.settings
    if not client.configured:
        return {"state": "NOT_CONFIGURED", "enabled": s.neo4j_enabled is not False, "latency_ms": None, "error": None}
    now = time.monotonic()
    if not force and client._health_cache and now - client._health_cache[0] < s.neo4j_health_cache_s:
        return client._health_cache[1]
    t0 = time.perf_counter()
    try:
        await client.run_read("RETURN 1 AS ok", timeout=min(s.neo4j_query_timeout_s, 5))
        body = {"state": "READY", "enabled": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": None}
    except GraphUnavailable as exc:
        body = {"state": exc.state, "enabled": True, "latency_ms": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - health must never raise
        body = {"state": "DEGRADED", "enabled": True, "latency_ms": None, "error": client._scrub(type(exc).__name__)}
    body["database"] = client.database
    client._health_cache = (now, body)
    return body


async def neo4j_state(client: GraphClient | None = None) -> str:
    from app.graph.client import get_graph_client

    return (await graph_health(client or get_graph_client()))["state"]
