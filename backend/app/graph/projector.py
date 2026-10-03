"""Idempotent outbox -> Neo4j projector.

* Pure part: `validate_plan` / `build_statements(rows)` turn claimed outbox rows into parameterized Cypher. Labels and
  relationship types come only from the allowlists in `app.graph.model` (they cannot be parameters); every value is a
  parameter (UNWIND $rows). All MERGEs are keyed by business ids, never Neo4j internal ids.
* Out-of-order tolerant: a relationship whose endpoint is not projected yet creates an endpoint stub
  (`projected=false`, organization_id, app) that the real node later fills (same MERGE key, so never duplicated).
  Mutable state is applied only when `state_at` is not older than what the node already holds.
* Effects: `drain()` claims rows with SKIP LOCKED, writes ONE Neo4j transaction per batch (run_write_batch), then marks
  the rows processed in the same Postgres transaction. Neo4j outage => rows stay pending, retried with backoff, never
  dead-lettered; poison rows (server rejects the Cypher / invalid plan) are isolated per row and dead-lettered after N tries.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_sessionmaker, utcnow
from app.graph import model as gm
from app.graph.client import GraphClient, get_graph_client
from app.graph.errors import GraphNotConfigured, GraphQueryError, GraphUnavailable, scrub
from app.graph.names import APP_NAMESPACE
from app.graph.outbox import CHECKPOINT_KEY, claim_batch, lag, mark_done, mark_failed
from app.graph.schema import NODE_IDS
from app.models.core import Setting
from app.models.graph_outbox import GraphOutbox

log = structlog.get_logger()
Statement = tuple[str, dict[str, Any]]


class PlanError(ValueError):
    """An outbox payload that can never be projected (poison)."""


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("v") != gm.PLAN_VERSION:
        raise PlanError(f"unsupported plan version {plan.get('v') if isinstance(plan, dict) else type(plan).__name__}")
    for n in plan.get("nodes") or []:
        if n.get("label") not in gm.LABELS or not str(n.get("id") or ""):
            raise PlanError(f"invalid node {n.get('label')!r}")
    for r in plan.get("rels") or []:
        if r.get("type") not in gm.REL_TYPES:
            raise PlanError(f"invalid relationship type {r.get('type')!r}")
        for end in (r.get("from"), r.get("to")):
            if not end or end[0] not in gm.LABELS or not str(end[1] or ""):
                raise PlanError(f"invalid relationship endpoint {end!r}")
    ev = plan.get("event")
    if ev is not None:
        if ev.get("type") not in gm.EVENT_TYPES:
            raise PlanError(f"invalid event type {ev.get('type')!r}")
        if ev.get("source") not in gm.SOURCES or ev.get("source_mode") not in gm.SOURCE_MODES:
            raise PlanError("event source/source_mode invalid")
        for end in [*(ev.get("about") or []), *([ev["emitted_by"]] if ev.get("emitted_by") else [])]:
            if end[0] not in gm.LABELS or not str(end[1] or ""):
                raise PlanError(f"invalid event endpoint {end!r}")
    return plan


def _node_q(label: str) -> str:
    idp = NODE_IDS[label]
    return (f"UNWIND $rows AS row MERGE (n:{label} {{{idp}: row.id}}) "
            "SET n += row.props, n.organization_id = row.node_org, n.app = $app, n.projected = true "
            "RETURN count(n) AS n, $organization_id AS scope")


def _state_q(label: str) -> str:
    idp = NODE_IDS[label]
    return (f"UNWIND $rows AS row MERGE (n:{label} {{{idp}: row.id}}) "
            "ON CREATE SET n.organization_id = row.node_org, n.app = $app, n.projected = false "
            "WITH n, row WHERE n.state_at IS NULL OR n.state_at <= row.state_at "
            "SET n += row.state, n.state_at = row.state_at "
            "RETURN count(n) AS n, $organization_id AS scope")


def _rel_q(l1: str, rtype: str, l2: str) -> str:
    k1, k2 = NODE_IDS[l1], NODE_IDS[l2]
    return (f"UNWIND $rows AS row "
            f"MERGE (a:{l1} {{{k1}: row.a}}) ON CREATE SET a.organization_id = row.org_a, a.app = $app, a.projected = false "
            f"MERGE (b:{l2} {{{k2}: row.b}}) ON CREATE SET b.organization_id = row.org_b, b.app = $app, b.projected = false "
            f"MERGE (a)-[r:{rtype}]->(b) "
            "SET r += row.props, r.organization_id = $organization_id, r.app = $app "
            "RETURN count(r) AS n")


_NEXT_DELETE = ("UNWIND $cs AS c MATCH (:Event {organization_id: $organization_id, correlation_id: c})-[r:NEXT]->(:Event) "
                "DELETE r")
_NEXT_BUILD = ("UNWIND $cs AS c MATCH (e:Event {organization_id: $organization_id, correlation_id: c}) "
               "WITH c, e ORDER BY e.occurred_at, e.event_id WITH c, collect(e) AS es "
               "UNWIND range(0, size(es) - 2) AS i WITH es[i] AS a, es[i + 1] AS b "
               "MERGE (a)-[r:NEXT]->(b) SET r.organization_id = $organization_id, r.app = $app")


def build_statements(rows: list[GraphOutbox]) -> list[Statement]:
    """Pure: outbox rows (in claim order) -> parameterized UNWIND statements, grouped per organization."""
    node_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    state_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    rel_rows: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    corr: dict[str, dict[str, None]] = defaultdict(dict)
    order: list[str] = []
    for r in rows:
        plan = validate_plan(r.payload)
        org = str(r.organization_id)
        if org not in order:
            order.append(org)
        for n in plan.get("nodes") or []:
            lab, nid = n["label"], str(n["id"])
            norg = gm.node_org(lab, org)
            if not n.get("touch"):
                props = {k: v for k, v in (n.get("props") or {}).items() if k not in ("organization_id", "app", NODE_IDS[lab])}
                node_rows[(org, lab)].append({"id": nid, "props": props, "node_org": norg})
            if n.get("state") and n.get("state_at"):
                state_rows[(org, lab)].append({"id": nid, "state": n["state"], "state_at": n["state_at"], "node_org": norg})
        for rel in plan.get("rels") or []:
            (l1, a), (l2, b) = rel["from"], rel["to"]
            rel_rows[(org, l1, rel["type"], l2)].append({
                "a": str(a), "b": str(b), "org_a": gm.node_org(l1, org), "org_b": gm.node_org(l2, org),
                "props": {k: v for k, v in (rel.get("props") or {}).items() if k not in ("organization_id", "app")}})
        ev = plan.get("event")
        if ev:
            eid = str(r.id)
            props = {**(ev.get("props") or {}), "event_type": ev["type"], "occurred_at": ev["occurred_at"],
                     "recorded_at": ev["recorded_at"], "source": ev["source"], "source_mode": ev["source_mode"],
                     "correlation_id": ev.get("correlation_id"), "aggregate_type": r.aggregate_type,
                     "aggregate_id": r.aggregate_id}
            node_rows[(org, "Event")].append({"id": eid, "props": {k: v for k, v in props.items() if v is not None},
                                              "node_org": org})
            for lab, nid in ev.get("about") or []:
                rel_rows[(org, "Event", "ABOUT", lab)].append({
                    "a": eid, "b": str(nid), "org_a": org, "org_b": gm.node_org(lab, org), "props": {}})
            if ev.get("emitted_by"):
                lab, nid = ev["emitted_by"]
                rel_rows[(org, lab, "EMITTED", "Event")].append({
                    "a": str(nid), "b": eid, "org_a": org, "org_b": org, "props": {}})
            if ev.get("correlation_id"):
                corr[org][str(ev["correlation_id"])] = None
    stmts: list[Statement] = []
    for org in order:
        base = {"organization_id": org, "app": APP_NAMESPACE}
        stmts += [(_node_q(lab), {**base, "rows": v}) for (o, lab), v in node_rows.items() if o == org]
        stmts += [(_state_q(lab), {**base, "rows": v}) for (o, lab), v in state_rows.items() if o == org]
        stmts += [(_rel_q(l1, t, l2), {**base, "rows": v}) for (o, l1, t, l2), v in rel_rows.items() if o == org]
        if corr.get(org):
            cs = list(corr[org])
            stmts.append((_NEXT_DELETE, {**base, "cs": cs}))
            stmts.append((_NEXT_BUILD, {**base, "cs": cs}))
    return stmts


# ---------------------------------------------------------------------------------------------------------------
async def _write_checkpoint(session, *, state: str, processed: int, error: str | None, last_created: Any) -> None:
    cp = await session.get(Setting, CHECKPOINT_KEY)
    prev = dict(cp.value) if cp else {}
    val = {
        "state": state, "processed_total": int(prev.get("processed_total", 0)) + processed,
        "last_run_at": utcnow().isoformat(), "last_error": error,
        "last_processed_created_at": (last_created.isoformat() if last_created else prev.get("last_processed_created_at")),
        "last_success_at": utcnow().isoformat() if state == "READY" and processed else prev.get("last_success_at"),
    }
    if cp is None:
        session.add(Setting(key=CHECKPOINT_KEY, value=val))
    else:
        cp.value = val


async def _apply(client: GraphClient, rows: list[GraphOutbox], timeout: float) -> None:
    await client.run_write_batch(build_statements(rows), timeout=timeout)


async def project_batch(session, client: GraphClient, rows: list[GraphOutbox]) -> dict[str, Any]:
    """Project already-claimed rows. Marks them in the caller's session (caller commits). Never raises for graph errors."""
    out = {"processed": 0, "failed": 0, "dead": 0, "outage": False, "error": None}
    timeout = max(30.0, get_settings().neo4j_query_timeout_s * 3)
    try:
        await _apply(client, rows, timeout)
    except GraphUnavailable as exc:
        msg = f"{type(exc).__name__}: {exc}"
        for r in rows:
            mark_failed(r, msg, outage=True)
        out.update(outage=True, error=msg, failed=len(rows))
        return out
    except (GraphQueryError, PlanError) as exc:
        log.warning("graph.batch_failed_isolating", error=str(exc)[:200], rows=len(rows))
        if len(rows) == 1:
            mark_failed(rows[0], scrub(f"{type(exc).__name__}: {exc}", get_settings().neo4j_password), outage=False)
            out.update(failed=1, error=str(exc)[:300], dead=int(rows[0].dead_at is not None))
            return out
        for r in rows:  # isolate the poison row(s); the rest still project
            sub = await project_batch(session, client, [r])
            for k in ("processed", "failed", "dead"):
                out[k] += sub[k]
            if sub["outage"]:
                out["outage"], out["error"] = True, sub["error"]
                break
        return out
    mark_done(rows)
    out["processed"] = len(rows)
    return out


async def drain(*, limit: int = 2000, batch_size: int | None = None, client: GraphClient | None = None,
                sessionmaker: async_sessionmaker | None = None, time_budget_s: float | None = None) -> dict[str, Any]:
    """Project pending outbox rows until empty, `limit` rows, the time budget, or an outage. Safe to run from many
    workers at once (SKIP LOCKED). Never raises for graph problems."""
    cli = client or get_graph_client()
    sm = sessionmaker or get_sessionmaker()
    bs = batch_size or get_settings().graph_outbox_batch
    t0 = time.monotonic()
    res: dict[str, Any] = {"state": "READY", "processed": 0, "failed": 0, "dead": 0, "batches": 0, "error": None}
    if not cli.configured:
        res["state"] = "NOT_CONFIGURED"
        return res
    if not getattr(cli, "_n2_schema_ok", False):  # uniqueness constraints make concurrent MERGEs safe
        try:
            from app.graph.schema import ensure_schema

            await ensure_schema(cli)
            cli._n2_schema_ok = True  # type: ignore[attr-defined]
        except GraphUnavailable as exc:
            res.update(state=exc.state, error=f"{type(exc).__name__}: {exc}")
            return res
    while res["processed"] + res["failed"] < limit:
        if time_budget_s is not None and time.monotonic() - t0 > time_budget_s:
            break
        async with sm() as session:
            rows = await claim_batch(session, bs)
            if not rows:
                break
            try:
                r = await project_batch(session, cli, rows)
            except GraphNotConfigured as exc:  # pragma: no cover - raced with config change
                await session.rollback()
                res.update(state="NOT_CONFIGURED", error=str(exc))
                break
            last = max((x.created_at for x in rows if x.processed_at is not None), default=None)
            state = "DEGRADED" if r["outage"] else "READY"
            await _write_checkpoint(session, state=state, processed=r["processed"], error=r["error"], last_created=last)
            await session.commit()
        res["batches"] += 1
        for k in ("processed", "failed", "dead"):
            res[k] += r[k]
        if r["outage"]:
            res.update(state="DEGRADED", error=r["error"])
            break
    async with sm() as session:
        res["lag"] = await lag(session)
    log.info("graph.projection", state=res["state"], processed=res["processed"], failed=res["failed"],
             dead=res["dead"], backlog=res["lag"]["backlog"], oldest_unprocessed_age_s=res["lag"]["oldest_unprocessed_age_s"])
    return res
