"""Replay / rebuild: reconstruct outbox rows from authoritative Postgres history, then project.

`replay()`  : history -> outbox (INSERT ... ON CONFLICT DO NOTHING on the deterministic event id, so events that already
              exist are skipped) -> projector drain. Idempotent; safe to run any time; never alters domain tables.
`rebuild()` : clear ONLY the application graph (nodes tagged app="profound-change-guard"), mark every outbox row pending
              again, replay, drain, then verify with the audit. Guarded: needs confirm=True AND env GRAPH_REBUILD_ALLOW=1.
CLI: `python -m app.graph.replay [--org UUID] [--no-project] | --rebuild --yes`  (make graph-replay / graph-rebuild).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker, utcnow
from app.graph import projector
from app.graph.client import GraphClient, get_graph_client
from app.graph.errors import GraphError
from app.graph.events import history_specs
from app.graph.outbox import _insert_stmt
from app.graph.schema import CLEAR_CONFIRMATION, clear_application_graph, ensure_schema
from app.models.graph_outbox import GraphOutbox

log = structlog.get_logger()
REBUILD_ENV = "GRAPH_REBUILD_ALLOW"


class RebuildRefused(GraphError):
    pass


def _enqueue_history(sync_session, organization_id: Any) -> dict[str, int]:
    conn = sync_session.connection()
    created = skipped = 0
    by_type: dict[str, int] = {}
    for spec in history_specs(sync_session, organization_id):
        res = conn.execute(_insert_stmt(conn.dialect.name, spec.row()))
        if res.rowcount:
            created += 1
            by_type[spec.event_type] = by_type.get(spec.event_type, 0) + 1
        else:
            skipped += 1
    return {"created": created, "skipped": skipped, **{f"created.{k}": v for k, v in sorted(by_type.items())}}


async def enqueue_history(session: AsyncSession, organization_id: Any = None) -> dict[str, int]:
    """History -> outbox rows (idempotent). The caller commits."""
    return await session.run_sync(_enqueue_history, organization_id)


async def replay(*, organization_id: Any = None, project: bool = True, client: GraphClient | None = None,
                 sessionmaker=None) -> dict[str, Any]:
    sm = sessionmaker or get_sessionmaker()
    async with sm() as session:
        stats = await enqueue_history(session, organization_id)
        await session.commit()
    out: dict[str, Any] = {"outbox": stats}
    if project:
        out["projection"] = await projector.drain(limit=1_000_000, client=client, sessionmaker=sm)
    return out


async def rebuild(*, confirm: bool = False, client: GraphClient | None = None, sessionmaker=None) -> dict[str, Any]:
    if not confirm:
        raise RebuildRefused("graph rebuild needs explicit confirmation (--yes)")
    if os.environ.get(REBUILD_ENV) != "1":
        raise RebuildRefused(f"graph rebuild is disabled: set {REBUILD_ENV}=1 to allow it")
    cli = client or get_graph_client()
    if not cli.configured:
        raise RebuildRefused("neo4j is not configured; nothing to rebuild")
    sm = sessionmaker or get_sessionmaker()
    removed = await clear_application_graph(cli, confirm=CLEAR_CONFIRMATION)
    await ensure_schema(cli)
    async with sm() as session:
        now = utcnow()
        res = await session.execute(update(GraphOutbox).where(GraphOutbox.processed_at.is_not(None) | GraphOutbox.dead_at.is_not(None))
                                    .values(processed_at=None, dead_at=None, attempts=0, last_error=None, next_attempt_at=now))
        reset = res.rowcount
        await session.commit()
    out = await replay(client=cli, sessionmaker=sm)
    from app.graph.audit import audit

    report = await audit(client=cli, sessionmaker=sm)
    return {"cleared_nodes": removed, "outbox_reset": reset, **out, "audit_ok": report["ok"],
            "audit_mismatches": report["mismatch_total"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild graph_outbox from Postgres history and project it to Neo4j")
    ap.add_argument("--org", help="limit to one organization id (replay only)")
    ap.add_argument("--no-project", action="store_true", help="only (re)fill the outbox")
    ap.add_argument("--rebuild", action="store_true", help="clear the application graph, replay everything, verify")
    ap.add_argument("--yes", action="store_true", help="confirm --rebuild")
    a = ap.parse_args(argv)

    async def run() -> int:
        try:
            if a.rebuild:
                if a.org:
                    print("--org is not supported with --rebuild (the whole application graph is cleared)", file=sys.stderr)
                    return 2
                out = await rebuild(confirm=a.yes)
                print(json.dumps(out, indent=2, default=str))
                return 0 if out["audit_ok"] else 1
            out = await replay(organization_id=a.org, project=not a.no_project)
            print(json.dumps(out, indent=2, default=str))
            proj = out.get("projection")
            return 0 if (proj is None or (proj["state"] == "READY" and proj["failed"] == 0)) else 1
        except RebuildRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        finally:
            from app.graph import close_graph_client

            await close_graph_client()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
