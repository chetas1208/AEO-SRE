"""Runtime capability detection: AuraDB only vs GDS plugin vs Aura Graph Analytics. Never assumes GDS."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.graph.errors import GraphQueryError, GraphUnavailable

if TYPE_CHECKING:
    from app.graph.client import GraphClient


async def detect_capabilities(client: GraphClient, *, force: bool = False) -> dict[str, Any]:
    s = client.settings
    base: dict[str, Any] = {
        "configured": client.configured, "server_version": None, "edition": None, "component": None, "aura_graph_analytics_hint": False,
        "database": client.database, "database_dedicated": None, "gds_plugin": "unknown", "gds_version": None,
        "gds_enabled": s.neo4j_gds_enabled, "gds_usable": False,
        "aura_graph_analytics": "not_configured" if not (s.neo4j_aura_api_client_id and s.neo4j_aura_api_client_secret)
        else "unknown",
        "tier": "unknown",
    }
    if not client.configured:
        base["gds_plugin"] = "not_configured"
        return base
    if client._caps_cache is not None and not force:
        return client._caps_cache
    try:
        rows = await client.run_read("CALL dbms.components() YIELD name, versions, edition "
                                     "RETURN name, versions, edition", timeout=5)
        if rows:
            base["server_version"] = (rows[0].get("versions") or [None])[0]
            base["edition"] = rows[0].get("edition")
            base["component"] = rows[0].get("name")
        base["database_dedicated"] = (client.database or "neo4j") not in ("neo4j", "system")
    except GraphUnavailable as exc:
        base["error"] = str(exc)
        return base  # not cached: connectivity problem, retry next call
    except GraphQueryError as exc:
        base["error"] = str(exc)
    try:
        rows = await client.run_read("RETURN gds.version() AS v", timeout=5)
        base["gds_plugin"] = "present"
        base["gds_version"] = rows[0]["v"] if rows else None
    except GraphQueryError as exc:  # ProcedureNotFound / unknown function / Aura stub: no GDS plugin
        base["gds_plugin"] = "absent"
        # Aura answers gds.version() with "Aura Graph Analytics is versionless": serverless analytics exist as a
        # product but need Aura API credentials to use; we only report the hint, never assume it.
        base["aura_graph_analytics_hint"] = "aura graph analytics" in str(exc).lower()
    except GraphUnavailable as exc:
        base["error"] = str(exc)
        return base
    base["gds_usable"] = base["gds_plugin"] == "present" and s.neo4j_gds_enabled
    base["tier"] = "gds_plugin" if base["gds_plugin"] == "present" else "auradb_only"
    client._caps_cache = base
    return base
