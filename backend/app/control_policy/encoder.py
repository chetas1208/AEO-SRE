"""control_context_v1: domain features + graph_context_v1 -> one fixed-order, versioned, normalized vector.

Layout (never reorder; a change = a new schema version + new PolicyVersion):
    DOMAIN_NAMES (domain block)  +  GRAPH_NAMES (23 graph_context_v1 values, already normalized to [0,1] by the graph
    feature encoder)  +  graph_available, graph_missing_fraction.
Missing graph features are explicit: their value is 0.0, `graph_available` tells whether a graph vector was supplied
and `graph_missing_fraction` the share of the 23 values the graph layer reported unknown. The names of the individual
missing features are returned in `EncodedContext.graph_missing` and persisted.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from app.changeguard.decision import (
    ACTIVE_EXPERIMENT,
    CANONICAL_CONFLICT,
    CANONICAL_UNCERTAIN,
    CONFLICTING_CHANGE,
    DUPLICATE_CHANGE,
    SEMANTIC_DEGRADED,
    Decision,
)
from app.graph.features import FEATURE_NAMES as GRAPH_FEATURE_NAMES

SCHEMA = "control_context_v1"
GRAPH_NAMES: tuple[str, ...] = tuple(GRAPH_FEATURE_NAMES)
DOMAIN_NAMES: tuple[str, ...] = (
    "bias",
    "action_observe", "action_update_existing_page", "action_create_content", "action_other",
    "source_simulated", "claims_count", "reversible_true", "reversible_false",
    "risk_low", "risk_medium", "risk_high",
    "finding_active_experiment", "finding_duplicate_change", "finding_conflicting_change",
    "finding_canonical_conflict", "finding_canonical_uncertain", "finding_semantic_degraded", "finding_count",
    "baseline_allow", "baseline_merge", "baseline_delay", "baseline_require_review", "baseline_block",
)
GRAPH_META_NAMES: tuple[str, ...] = ("graph_available", "graph_missing_fraction")
FEATURE_NAMES: tuple[str, ...] = DOMAIN_NAMES + GRAPH_NAMES + GRAPH_META_NAMES
DIM = len(FEATURE_NAMES)
DOMAIN_DIM = len(DOMAIN_NAMES)
DOMAIN_COLUMNS: tuple[int, ...] = tuple(range(DOMAIN_DIM))
ALL_COLUMNS: tuple[int, ...] = tuple(range(DIM))
_CREATE = {"create_faq", "create_canonical_page", "create_comparison_content"}
_FINDING_FLAGS = {
    ACTIVE_EXPERIMENT: "finding_active_experiment", DUPLICATE_CHANGE: "finding_duplicate_change",
    CONFLICTING_CHANGE: "finding_conflicting_change", CANONICAL_CONFLICT: "finding_canonical_conflict",
    CANONICAL_UNCERTAIN: "finding_canonical_uncertain", SEMANTIC_DEGRADED: "finding_semantic_degraded"}


def schema_hash() -> str:
    """Stable hash of (schema name, ordered feature names): any reorder/rename/addition changes it."""
    return hashlib.sha256(json.dumps([SCHEMA, list(FEATURE_NAMES)]).encode()).hexdigest()


@dataclass
class DomainContext:
    """What the policy knows about a ChangeSet without the graph (all persisted by Change Guard)."""

    action_type: str = ""
    source_mode: str = "LIVE"
    claims_count: int = 0
    reversible: bool | None = None
    risk: str | None = None
    finding_types: list[str] = field(default_factory=list)
    baseline_decision: Decision = Decision.ALLOW

    def to_json(self) -> dict[str, Any]:
        return {"action_type": self.action_type, "source_mode": self.source_mode, "claims_count": self.claims_count,
                "reversible": self.reversible, "risk": self.risk, "finding_types": sorted(self.finding_types),
                "baseline_decision": self.baseline_decision.value}


@dataclass
class EncodedContext:
    vector: list[float]
    names: tuple[str, ...]
    schema: str
    hash: str
    graph_available: bool
    graph_missing: list[str]
    graph_version: str | None = None
    graph_context_hash: str | None = None
    graph_snapshot_time: Any = None
    graph_source: str | None = None


def domain_features(d: DomainContext) -> dict[str, float]:
    a = d.action_type
    v = dict.fromkeys(DOMAIN_NAMES, 0.0)
    v["bias"] = 1.0
    v["action_observe"] = float(a == "observe")
    v["action_update_existing_page"] = float(a == "update_existing_page")
    v["action_create_content"] = float(a in _CREATE)
    v["action_other"] = float(bool(a) and a != "observe" and a != "update_existing_page" and a not in _CREATE)
    v["source_simulated"] = float(d.source_mode == "SIMULATED")
    v["claims_count"] = min(math.log1p(max(d.claims_count, 0)) / math.log1p(10), 1.0)
    v["reversible_true"] = float(d.reversible is True)
    v["reversible_false"] = float(d.reversible is False)  # both 0 = unknown
    r = (d.risk or "").lower()
    v["risk_low"], v["risk_medium"], v["risk_high"] = float(r == "low"), float(r == "medium"), float(r == "high")
    for t in set(d.finding_types):
        if t in _FINDING_FLAGS:
            v[_FINDING_FLAGS[t]] = 1.0
    v["finding_count"] = min(len(d.finding_types) / 5.0, 1.0)
    v[f"baseline_{d.baseline_decision.value.lower()}"] = 1.0
    return v


def _graph_values(graph: Any) -> tuple[dict[str, float], list[str]]:
    """(name -> normalized value, missing names) from a GraphFeatures-like object (vector+names, optional missing)."""
    names = list(getattr(graph, "names", []) or [])
    vec = list(getattr(graph, "vector", []) or [])
    by_name = dict(zip(names, vec, strict=False))
    missing = list(getattr(graph, "missing", []) or [])
    if not missing:
        missing = [n for n in GRAPH_NAMES if by_name.get(f"{n}__missing", 0.0) >= 0.5]
    vals = {n: float(by_name[n]) if n in by_name and n not in missing else 0.0 for n in GRAPH_NAMES}
    for n in GRAPH_NAMES:
        vals[n] = max(0.0, min(1.0, vals[n]))
    return vals, [n for n in GRAPH_NAMES if n in missing]


def encode(domain: DomainContext, graph: Any | None = None) -> EncodedContext:
    dv = domain_features(domain)
    usable = graph is not None and bool(getattr(graph, "vector", None)) and not getattr(graph, "unavailable", False)
    if usable:
        gv, missing = _graph_values(graph)
    else:
        gv, missing = dict.fromkeys(GRAPH_NAMES, 0.0), list(GRAPH_NAMES)
    meta = {"graph_available": 1.0 if usable else 0.0, "graph_missing_fraction": len(missing) / len(GRAPH_NAMES)}
    allv = {**dv, **gv, **meta}
    vector = [round(allv[n], 6) for n in FEATURE_NAMES]
    return EncodedContext(
        vector=vector, names=FEATURE_NAMES, schema=SCHEMA, hash=vector_hash(vector), graph_available=usable,
        graph_missing=missing, graph_version=getattr(graph, "version", None) if usable else None,
        graph_context_hash=getattr(graph, "context_hash", None) if usable else None,
        graph_snapshot_time=getattr(graph, "snapshot_time", None) if usable else None,
        graph_source=getattr(graph, "source", None) if graph is not None else None)


def vector_hash(vector: list[float], schema: str = SCHEMA) -> str:
    return hashlib.sha256(json.dumps([schema, [round(float(x), 6) for x in vector]],
                                     separators=(",", ":")).encode()).hexdigest()
