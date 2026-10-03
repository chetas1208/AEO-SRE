"""Neo4j Aura smoke. `make neo4j-smoke` / cd backend && .venv/bin/python -m app.devtools.neo4j_smoke

validate config -> secure connect -> RETURN 1 -> version/database metadata -> write+read+delete a temporary uniquely
named test node (app="neo4j-smoke") -> latency report. Never prints the password.
Exit codes: 0 ready, 1 degraded, 2 not configured, 3 auth failed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

from app.core.config import get_settings
from app.graph.client import GraphClient
from app.graph.errors import GraphAuthFailed, GraphNotConfigured, GraphUnavailable

EXIT = {"READY": 0, "DEGRADED": 1, "NOT_CONFIGURED": 2, "AUTH_FAILED": 3}


async def run() -> tuple[dict, str]:
    s = get_settings()
    report: dict = {
        "uri_scheme": s.neo4j_uri.split("://")[0] if "://" in s.neo4j_uri else ("unset" if not s.neo4j_uri else "invalid"),
        "uri_host": (s.neo4j_uri.split("://")[-1].split("@")[-1] if s.neo4j_uri else None),
        "username": s.neo4j_username, "password": "set" if s.neo4j_password else "unset",
        "database": s.neo4j_database or "(default)", "enabled": s.neo4j_active,
    }
    client = GraphClient()
    if not client.configured:
        return {**report, "state": "NOT_CONFIGURED"}, "NOT_CONFIGURED"
    try:
        t0 = time.perf_counter()
        await client.verify()
        report["connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        t0 = time.perf_counter()
        await client.run_read("RETURN 1 AS ok")
        report["return1_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        report["capabilities"] = await client.capabilities(force=True)
        tid = uuid.uuid4().hex
        node = {"tid": tid, "app": "neo4j-smoke"}
        t0 = time.perf_counter()
        await client.run_write("CREATE (n:SmokeTest {tid: $tid, app: $app, organization_id: 'smoke'})", node)
        got = await client.run_read("MATCH (n:SmokeTest {tid: $tid, app: $app}) RETURN count(n) AS c", node)
        await client.run_write("MATCH (n:SmokeTest {tid: $tid, app: $app}) DETACH DELETE n", node)
        left = await client.run_read("MATCH (n:SmokeTest {tid: $tid}) RETURN count(n) AS c", node)
        report["write_read_delete_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        report["roundtrip"] = {"created_and_read": got[0]["c"], "remaining_after_delete": left[0]["c"]}
        if got[0]["c"] != 1 or left[0]["c"] != 0:
            return {**report, "state": "DEGRADED"}, "DEGRADED"
        return {**report, "state": "READY"}, "READY"
    except GraphNotConfigured:
        return {**report, "state": "NOT_CONFIGURED"}, "NOT_CONFIGURED"
    except GraphAuthFailed:
        return {**report, "state": "AUTH_FAILED"}, "AUTH_FAILED"
    except GraphUnavailable as exc:
        return {**report, "state": "DEGRADED", "error": str(exc)}, "DEGRADED"
    except Exception as exc:  # noqa: BLE001
        return {**report, "state": "DEGRADED", "error": client._scrub(f"{type(exc).__name__}: {exc}")}, "DEGRADED"
    finally:
        await client.close()


def main() -> int:
    report, state = asyncio.run(run())
    print(json.dumps(report, indent=2, default=str))
    return EXIT[state]


if __name__ == "__main__":
    sys.exit(main())
