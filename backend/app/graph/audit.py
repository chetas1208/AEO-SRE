"""graph-audit: READ-ONLY Postgres <-> Neo4j reconciliation (counts + id sets). Never mutates either side.

Expected nodes are derived from authoritative Postgres history with the SAME mapping the projector uses
(`events.history_specs`), so a node deleted from the graph (or never projected) shows up as `missing`, a node with no
Postgres origin as `extra`, and an endpoint stub that was never filled (`projected=false`) as `unfilled_stubs`.
Events: Postgres side = domain-derived event ids UNION outbox rows that carry an event (the outbox is the event log).
CLI: `python -m app.graph.audit [--org UUID] [--cap N] [--json]`; exit 0 in sync, 1 on any mismatch, 2 not configured.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.graph import model as gm
from app.graph.client import GraphClient, get_graph_client
from app.graph.errors import GraphUnavailable
from app.graph.events import history_specs
from app.graph.names import APP_NAMESPACE
from app.graph.outbox import lag
from app.graph.schema import NODE_IDS
from app.models.graph_outbox import GraphOutbox

REPORT_LABELS = ["ChangeSet", "Conflict", "Decision", "Experiment", "Observation", "PolicyDecision", "Agent", "AgentRun",
                 "Event"]
_ID_CASE = "CASE labels(n)[0] " + " ".join(f"WHEN '{lbl}' THEN n.{p}" for lbl, p in NODE_IDS.items()) + " END"
_ALL_ORGS = "MATCH (n {app: $app}) RETURN DISTINCT n.organization_id AS org"
_ORG_NODES = (f"MATCH (n {{app: $app, organization_id: $organization_id}}) "
              f"RETURN labels(n)[0] AS label, {_ID_CASE} AS id, coalesce(n.projected, true) AS projected")


def _expected(sync_session, organization_id: Any) -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    exp: dict[tuple[str, str], set[str]] = {}
    events: set[str] = set()
    for spec in history_specs(sync_session, organization_id):
        plan = spec.payload
        for n in plan.get("nodes") or []:
            if n.get("touch"):
                continue
            exp.setdefault((gm.node_org(n["label"], str(spec.organization_id)), n["label"]), set()).add(str(n["id"]))
        if plan.get("event"):
            events.add(str(spec.id))
            exp.setdefault((str(spec.organization_id), "Event"), set()).add(str(spec.id))
    return exp, events


def _cap(ids: set[str], cap: int) -> list[str]:
    return sorted(ids)[:cap]


async def audit(*, organization_id: Any = None, cap: int = 20, client: GraphClient | None = None,
                sessionmaker=None) -> dict[str, Any]:
    cli = client or get_graph_client()
    sm = sessionmaker or get_sessionmaker()
    org_filter = str(organization_id) if organization_id else None
    async with sm() as session:
        expected, _ = await session.run_sync(_expected, organization_id)
        rows = (await session.execute(select(GraphOutbox.id, GraphOutbox.organization_id, GraphOutbox.payload))).all()
        for oid, org, payload in rows:
            if (payload or {}).get("event") and (not org_filter or str(org) == org_filter):
                expected.setdefault((str(org), "Event"), set()).add(str(oid))
        backlog = await lag(session)
    report: dict[str, Any] = {"organizations": {}, "labels": {}, "outbox": backlog, "ok": False, "mismatch_total": 0}
    if not cli.configured:
        report["state"] = "NOT_CONFIGURED"
        return report
    actual: dict[tuple[str, str], dict[str, bool]] = {}
    try:
        if org_filter:
            orgs = {org_filter}
            if any(o == gm.GLOBAL_ORG for (o, _l) in expected):
                orgs.add(gm.GLOBAL_ORG)
        else:
            orgs = {r["org"] for r in await cli.run_read(_ALL_ORGS, {"app": APP_NAMESPACE}) if r["org"]}
        orgs |= {o for (o, _l) in expected}
        for org in sorted(orgs):
            for r in await cli.scoped_read(org, _ORG_NODES, {"app": APP_NAMESPACE}, timeout=60):
                if r["id"] is not None:
                    actual.setdefault((org, r["label"]), {})[str(r["id"])] = bool(r["projected"])
    except GraphUnavailable as exc:
        report.update(state=exc.state, error=str(exc))
        return report
    report["state"] = "READY"
    total = 0
    per_label: dict[str, dict[str, Any]] = {}
    keys = set(expected) | set(actual)
    for (org, label) in sorted(keys):
        want = expected.get((org, label), set())
        got = actual.get((org, label), {})
        real = {i for i, p in got.items() if p}
        stubs = {i for i, p in got.items() if not p}
        missing = want - real
        extra = real - want
        unfilled = stubs - want  # stubs for ids Postgres expects are already counted as missing
        entry = {"postgres": len(want), "neo4j": len(real), "missing": len(missing), "extra": len(extra),
                 "unfilled_stubs": len(unfilled), "missing_ids": _cap(missing, cap), "extra_ids": _cap(extra, cap),
                 "unfilled_stub_ids": _cap(unfilled, cap)}
        report["organizations"].setdefault(org, {})[label] = entry
        agg = per_label.setdefault(label, {"postgres": 0, "neo4j": 0, "missing": 0, "extra": 0, "unfilled_stubs": 0})
        for k in ("postgres", "neo4j", "missing", "extra", "unfilled_stubs"):
            agg[k] += entry[k]
        total += len(missing) + len(extra) + len(unfilled)
    report["labels"] = dict(sorted(per_label.items()))
    report["mismatch_total"] = total
    report["ok"] = total == 0
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only Postgres <-> Neo4j reconciliation")
    ap.add_argument("--org")
    ap.add_argument("--cap", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    async def run() -> int:
        try:
            rep = await audit(organization_id=a.org, cap=a.cap)
        finally:
            from app.graph import close_graph_client

            await close_graph_client()
        if a.json:
            print(json.dumps(rep, indent=2, default=str))
        else:
            print(f"graph-audit state={rep.get('state')} ok={rep['ok']} mismatches={rep['mismatch_total']} "
                  f"backlog={rep['outbox']['backlog']} oldest_age_s={rep['outbox']['oldest_unprocessed_age_s']}")
            for label, e in rep["labels"].items():
                flag = "" if not (e["missing"] or e["extra"] or e["unfilled_stubs"]) else "  <-- MISMATCH"
                print(f"  {label:15s} postgres={e['postgres']:5d} neo4j={e['neo4j']:5d} missing={e['missing']} "
                      f"extra={e['extra']} stubs={e['unfilled_stubs']}{flag}")
            for org, labels in rep["organizations"].items():
                for label, e in labels.items():
                    if e["missing_ids"] or e["extra_ids"] or e["unfilled_stub_ids"]:
                        print(f"  org={org} {label}: missing={e['missing_ids']} extra={e['extra_ids']} "
                              f"stubs={e['unfilled_stub_ids']}")
        if rep.get("state") == "NOT_CONFIGURED":
            return 2
        return 0 if rep["ok"] else 1

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
