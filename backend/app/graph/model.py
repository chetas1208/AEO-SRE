"""Graph model contract (N2). The single place that names node labels, relationship types and id rules.

Everything is keyed by BUSINESS ids (Postgres UUIDs, deterministic uuid5 ids, or org-namespaced strings), never by
Neo4j internal ids. Every node carries `organization_id`, `app` ("profound-change-guard") and `projected`
(true = real node filled from an event; false = endpoint stub created by a relationship that arrived first).
Timestamps are ISO-8601 UTC strings with microseconds ("2026-10-03T21:13:51.986871Z"; lexicographic order == time order).

See docs/notes/handoff-n2.md for the full catalogue (properties per label, relationship directions, event mapping).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.graph.schema import NODE_IDS

GLOBAL_ORG = "GLOBAL"  # PolicyVersion rows are not organization data; the node (and policy-update events) use this
NAMESPACE = uuid.UUID("6f1c2a9e-4d3b-5c7a-9e21-0b6a7d8c9e10")  # uuid5 namespace for every derived id
PLAN_VERSION = 1  # outbox payload / plan schema_version

SOURCES = ("PROFOUND", "CHANGE_GUARD", "HUMAN", "SYSTEM")
SOURCE_MODES = ("LIVE", "SIMULATED", "FIXTURE", "TEST")

LABELS = frozenset(NODE_IDS)
REL_TYPES = frozenset({
    "STARTED", "EMITTED", "PROPOSED", "MODIFIES", "ALTERS", "ABOUT", "AFFECTS", "CONFLICTS_WITH", "INVOLVES",
    "PROTECTS", "DECIDES_ON", "BASED_ON", "APPROVES", "EXECUTED_AS", "MEASURES", "OBSERVED", "PRODUCED", "SELECTED",
    "USED", "TRIGGERED_BY", "DERIVED_FROM", "FOLLOWED_BY", "ASSOCIATED_WITH", "NEXT",
})
# Deliberately absent: CAUSED_BY (never inferred from temporal order alone).

EVENT_TYPES = frozenset({
    "CHANGE_PROPOSED", "CONFLICT_DETECTED", "DECISION_CREATED", "APPROVAL_GRANTED", "APPROVAL_REJECTED",
    "CHANGE_EXECUTED", "CHANGE_EXECUTION_FAILED", "EXPERIMENT_STARTED", "EXPERIMENT_VERIFIED",
    "OBSERVATION_RECORDED", "OUTCOME_MEASURED", "REWARD_CREATED", "POLICY_UPDATED", "INCIDENT_CREATED",
    "CANONICAL_CLAIM_CHANGED",
})
# Spec event types with no separate real step in this domain: TARGET_RESOLVED and CLAIMS_EXTRACTED happen inside
# CHANGE_PROPOSED (the Target / Claim nodes and MODIFIES / ALTERS edges carry them).

AGGREGATE_TYPES = frozenset({
    "organization", "prompt_cluster", "incident", "intervention", "change_set", "change_check", "approval",
    "execution", "experiment", "observation", "outcome", "reward", "policy_decision", "policy_version",
    "canonical_claim",
})


def iso(dt: datetime | str | None) -> str | None:
    """Canonical timestamp string (UTC, microseconds, trailing Z)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def derived_id(*parts: Any) -> str:
    """Deterministic uuid5 over the parts (event ids, conflict ids, claim ids, ...)."""
    return str(uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts)))


def event_id(aggregate_type: str, aggregate_id: Any, event_type: str, version: Any) -> uuid.UUID:
    """Outbox id == Event node id: uuid5 of aggregate + type + version (replay skips ids that already exist)."""
    return uuid.UUID(derived_id(aggregate_type, aggregate_id, event_type, version))


def agent_node_id(org_id: Any, agent_ref: str) -> str:
    return f"{org_id}:{agent_ref}"


def run_node_id(org_id: Any, agent_ref: str, run_ref: str) -> str:
    return f"{org_id}:{agent_ref}:{run_ref}"


def target_node_id(org_id: Any, normalized: str) -> str:
    return f"{org_id}:{normalized}"


def claim_node_id(org_id: Any, text: str) -> str:
    return derived_id("claim", org_id, " ".join(str(text).split()).casefold())


def conflict_node_id(check_id: Any, index: int, ftype: str) -> str:
    return derived_id("conflict", check_id, index, ftype)


def node_org(label: str, default: str) -> str:
    return GLOBAL_ORG if label == "PolicyVersion" else default


def clean_props(props: dict[str, Any] | None) -> dict[str, Any]:
    """Neo4j property values must be primitives or homogeneous primitive lists: drop None, JSON-encode the rest."""
    import json

    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if v is None:
            continue
        if isinstance(v, bool | int | float | str):
            out[k] = v
        elif isinstance(v, datetime):
            out[k] = iso(v)
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, list | tuple) and all(isinstance(x, str) for x in v):
            out[k] = list(v)
        else:
            out[k] = json.dumps(v, sort_keys=True, default=str)
    return out
