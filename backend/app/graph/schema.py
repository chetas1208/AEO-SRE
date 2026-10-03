"""Graph schema: uniqueness constraints on business ids, hot-property indexes, node-property contract.

Node contract (every node, no exceptions): `<id_prop>` (Postgres UUID / event id; never Neo4j internal ids),
`organization_id`, `app` = "profound-change-guard". Use `merge_node()` so the MERGE key is only the business id
(constraint-safe: organization_id/app are SET, never part of the MERGE pattern).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

from app.graph.errors import GraphError, require_confirm_error
from app.graph.names import APP_NAMESPACE  # noqa: F401  (re-exported)

if TYPE_CHECKING:
    from app.graph.client import GraphClient

log = structlog.get_logger()
SCHEMA_VERSION = 1
CLEAR_CONFIRMATION = "DELETE-APPLICATION-GRAPH"

# label -> business id property (uniqueness constraint)
NODE_IDS: dict[str, str] = {
    "Organization": "organization_id", "Event": "event_id", "Agent": "agent_id", "AgentRun": "agent_run_id",
    "ChangeSet": "changeset_id", "Target": "target_id", "Claim": "claim_id", "Conflict": "conflict_id",
    "Decision": "decision_id", "Approval": "approval_id", "Experiment": "experiment_id",
    "Observation": "observation_id", "Outcome": "outcome_id", "PolicyDecision": "policy_decision_id",
    "PolicyVersion": "policy_version_id", "PromptCluster": "prompt_cluster_id",
    "Incident": "incident_id", "Intervention": "intervention_id",  # N2: real linked domain objects
}
# (label, property) hot-property indexes only
INDEXES: list[tuple[str, str]] = [
    ("Event", "occurred_at"), ("Event", "event_type"), ("Event", "organization_id"),
    ("ChangeSet", "organization_id"), ("ChangeSet", "status"), ("ChangeSet", "occurred_at"),
    ("Target", "organization_id"), ("Agent", "organization_id"), ("Conflict", "organization_id"),
    ("Conflict", "status"), ("Decision", "organization_id"), ("Decision", "decision"),
    ("Decision", "occurred_at"), ("Experiment", "organization_id"), ("Experiment", "status"),
    ("Event", "correlation_id"),  # N2: NEXT chains are rebuilt per correlation id
]


def constraint_name(label: str, prop: str) -> str:
    return f"cg_{label.lower()}_{prop}_unique"


def index_name(label: str, prop: str) -> str:
    return f"cg_{label.lower()}_{prop}_idx"


def schema_statements() -> list[str]:
    stmts = [
        f"CREATE CONSTRAINT {constraint_name(lbl, prop)} IF NOT EXISTS FOR (n:{lbl}) REQUIRE n.{prop} IS UNIQUE"
        for lbl, prop in NODE_IDS.items()
    ]
    stmts += [f"CREATE INDEX {index_name(lbl, prop)} IF NOT EXISTS FOR (n:{lbl}) ON (n.{prop})"
              for lbl, prop in INDEXES]
    return stmts


async def ensure_schema(client: GraphClient) -> dict[str, Any]:
    """Idempotent. Schema DDL cannot share a transaction with writes, so each statement runs on its own."""
    for stmt in schema_statements():
        await client.run_write(stmt, timeout=30)
    return await describe_schema(client)


async def describe_schema(client: GraphClient) -> dict[str, Any]:
    cons = await client.run_read("SHOW CONSTRAINTS YIELD name, type RETURN name, type", timeout=15)
    idx = await client.run_read("SHOW INDEXES YIELD name, type, state RETURN name, type, state", timeout=15)
    ours_c = {r["name"] for r in cons if str(r["name"]).startswith("cg_")}
    ours_i = {r["name"] for r in idx if str(r["name"]).startswith("cg_")}
    want_c = {constraint_name(lbl, p) for lbl, p in NODE_IDS.items()}
    want_i = {index_name(lbl, p) for lbl, p in INDEXES}
    return {
        "schema_version": SCHEMA_VERSION, "constraints": len(ours_c), "indexes": len(ours_i),
        "missing_constraints": sorted(want_c - ours_c), "missing_indexes": sorted(want_i - ours_i),
        "complete": want_c <= ours_c and want_i <= ours_i,
    }


def node_props(organization_id: Any, props: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Property map carrying the mandatory organization_id and app namespace."""
    from app.graph.client import require_org

    out = {k: v for k, v in (props or {}).items() if k not in ("organization_id", "app")}
    out["organization_id"] = require_org(organization_id)
    out["app"] = APP_NAMESPACE
    return out


def merge_node(label: str, organization_id: Any, node_id: Any, props: Mapping[str, Any] | None = None
               ) -> tuple[str, dict[str, Any]]:
    """Idempotent upsert keyed ONLY by the business id. Labels come from the allowlist (cannot be parameterized);
    everything else is a parameter. Returns (cypher, params)."""
    if label not in NODE_IDS:
        raise GraphError(f"unknown graph label {label!r}")
    id_prop = NODE_IDS[label]
    if node_id is None or str(node_id) == "":
        raise GraphError(f"{label}.{id_prop} is required")
    p = node_props(organization_id, props)
    p.pop(id_prop, None)
    cypher = f"MERGE (n:{label} {{{id_prop}: $id}}) SET n += $props RETURN n.{id_prop} AS id"
    return cypher, {"id": str(node_id), "props": p}


async def clear_application_graph(client: GraphClient, *, confirm: str, batch_size: int = 5000) -> int:
    """Delete ONLY nodes tagged app="profound-change-guard" (batched DETACH DELETE). Requires the exact
    confirmation string. Refuses the `system` database. Returns the number of nodes removed."""
    if confirm != CLEAR_CONFIRMATION:
        raise require_confirm_error()
    if not client.configured:
        from app.graph.errors import GraphNotConfigured

        raise GraphNotConfigured("neo4j is not configured")
    if (client.database or "") == "system":
        raise GraphError("refusing to clear the system database")
    total = 0
    while True:
        rows = await client.run_write(
            "MATCH (n {app: $app}) WITH n LIMIT $n DETACH DELETE n RETURN count(*) AS c",
            {"app": APP_NAMESPACE, "n": int(batch_size)}, timeout=60,
        )
        deleted = int(rows[0]["c"]) if rows else 0
        total += deleted
        if deleted < batch_size:
            break
    log.info("graph.cleared", nodes=total, database=client.database)
    return total
