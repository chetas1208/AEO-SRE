"""graph_context_v1: feature computation (pure), fixed-order encoder, context hash.

Everything here is a pure function of (facts, as_of). Facts are fetched by `GraphRepository`; this module also
re-applies the `occurred_at <= as_of` filter so leakage is impossible even if a caller hands in unfiltered facts.
Definitions: docs/notes/handoff-n3.md.

Unknown is never a silent 0: a feature whose inputs are unavailable is `None` -> value 0.0 in the vector AND the
matching `<name>__missing` flag = 1.0.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

FEATURE_VERSION = "graph_context_v1"
RECENT_WINDOW = timedelta(days=7)
H1, H24 = timedelta(hours=1), timedelta(hours=24)
MAX_PATH_HOPS = 4
UNREACHABLE_HOPS = MAX_PATH_HOPS + 1  # "no active experiment within the bound" (known, not missing)
MAX_DEPENDENCY_DEPTH = 4
TERMINAL_STATUSES = frozenset({"EXECUTED", "APPLIED", "REJECTED", "BLOCKED", "CANCELLED", "CANCELED", "VERIFIED",
                               "EXPIRED", "COMPLETED", "FAILED"})
ACTIVE_EXPERIMENT_STATUSES = frozenset({"RUNNING", "ACTIVE", "MEASURING", "IN_PROGRESS", "STARTED"})

# (name, kind, cap) in the FIXED encoder order. kind: count | rate | seconds | hops | depth
FEATURE_SPECS: tuple[tuple[str, str, float], ...] = (
    ("target_degree", "count", 50), ("target_recent_change_count", "count", 20),
    ("target_unique_agent_count", "count", 10), ("target_active_experiment_count", "count", 5),
    ("changeset_conflict_degree", "count", 10), ("duplicate_neighbor_count", "count", 10),
    ("canonical_conflict_count", "count", 10), ("prompt_overlap_count", "count", 20),
    ("agent_recent_conflict_rate", "rate", 1), ("agent_target_history_count", "count", 20),
    ("shortest_path_to_active_experiment", "hops", UNREACHABLE_HOPS), ("protected_targets_touched", "count", 5),
    ("dependency_depth", "depth", MAX_DEPENDENCY_DEPTH), ("pending_predecessor_count", "count", 10),
    ("historical_similar_context_count", "count", 50), ("historical_allow_rate", "rate", 1),
    ("historical_block_rate", "rate", 1), ("historical_review_rate", "rate", 1),
    ("seconds_since_last_target_change", "seconds", RECENT_WINDOW.total_seconds()),
    ("changes_on_target_last_1h", "count", 10), ("changes_on_target_last_24h", "count", 30),
    ("agent_changes_last_1h", "count", 10), ("conflicts_last_24h", "count", 20),
)
FEATURE_NAMES: tuple[str, ...] = tuple(s[0] for s in FEATURE_SPECS)
MISSING_NAMES: tuple[str, ...] = tuple(f"{n}__missing" for n in FEATURE_NAMES)
VECTOR_NAMES: tuple[str, ...] = FEATURE_NAMES + MISSING_NAMES
VECTOR_DIM = len(VECTOR_NAMES)


# --------------------------------------------------------------------------------------------- facts (inputs)
@dataclass
class ConflictFact:
    id: str
    type: str | None = None
    at: datetime | None = None
    others: list[str] = field(default_factory=list)  # other ChangeSet ids sharing the conflict


@dataclass
class ChangeFact:
    id: str
    at: datetime | None
    agent: str | None = None
    conflicts: list[ConflictFact] = field(default_factory=list)
    target_ids: list[str] = field(default_factory=list)


@dataclass
class ExperimentFact:
    id: str
    started_at: datetime | None = None
    ends_at: datetime | None = None
    status: str | None = None


@dataclass
class TargetFacts:
    id: str
    key: str | None = None
    protected: bool = False
    changes: list[ChangeFact] = field(default_factory=list)
    experiments: list[ExperimentFact] = field(default_factory=list)
    neighbors: list[tuple[str, str, datetime | None]] = field(default_factory=list)  # (label, id, at)
    conflicts: list[ConflictFact] = field(default_factory=list)  # conflicts AFFECTing the target


@dataclass
class ContextFacts:
    organization_id: str
    changeset_id: str | None = None
    changeset_found: bool = False
    agent_id: str | None = None
    prompt_cluster_ids: list[str] = field(default_factory=list)
    targets: list[TargetFacts] = field(default_factory=list)
    agent_changes: list[ChangeFact] | None = None  # None = no agent in context
    own_conflicts: list[ConflictFact] | None = None  # None = changeset not in the graph
    prompt_overlap: dict[str, list[tuple[str, datetime | None]]] | None = None  # None = no prompt clusters known
    dependency_depth: int | None = None
    predecessors: list[tuple[str, str | None]] = field(default_factory=list)  # (id, status)
    path_hops: int | None = None  # None = no active experiment within the bound
    path_checked: bool = False
    similar_decisions: list[str] = field(default_factory=list)  # decisions of similar historical contexts
    similar_checked: bool = False


# ----------------------------------------------------------------------------------------------- helpers
def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _le(at: datetime | None, as_of: datetime) -> bool:
    return at is not None and as_utc(at) <= as_of


def _known_before(at: datetime | None, as_of: datetime) -> bool:
    """Nodes without a timestamp (Target, Claim, Experiment...) are structural and always visible."""
    return at is None or as_utc(at) <= as_of


def experiment_active(e: ExperimentFact, as_of: datetime) -> bool:
    if e.started_at is not None and as_utc(e.started_at) > as_of:
        return False
    if e.ends_at is not None:
        return as_utc(e.ends_at) > as_of
    if e.started_at is not None:
        return True
    return (e.status or "").upper() in ACTIVE_EXPERIMENT_STATUSES


def _is_duplicate(ctype: str | None) -> bool:
    return "duplicate" in (ctype or "").lower()


def _is_canonical(ctype: str | None) -> bool:
    t = (ctype or "").lower()
    return "canonical" in t or "contradict" in t


# ---------------------------------------------------------------------------------------------- compute
def compute_features(facts: ContextFacts, as_of: datetime) -> dict[str, float | None]:
    """The 23 graph_context_v1 values (None = unknown). History strictly `occurred_at <= as_of`, focal ChangeSet
    excluded from its own history."""
    A = as_utc(as_of)
    self_id = facts.changeset_id
    out: dict[str, float | None] = dict.fromkeys(FEATURE_NAMES)

    # ---- target-centred
    targets = facts.targets
    if targets:
        changes: dict[str, ChangeFact] = {}
        for t in targets:
            for c in t.changes:
                if c.id != self_id and _le(c.at, A):
                    changes.setdefault(c.id, c)
        times = [as_utc(c.at) for c in changes.values() if c.at is not None]
        neigh: set[tuple[str, str]] = set()
        for t in targets:
            for label, nid, at in t.neighbors:
                if nid != self_id and _known_before(at, A):
                    neigh.add((label, nid))
        active_exps = {e.id for t in targets for e in t.experiments if experiment_active(e, A)}
        conflicts: dict[str, ConflictFact] = {}
        for t in targets:
            for k in t.conflicts:
                conflicts.setdefault(k.id, k)
        for c in changes.values():
            for k in c.conflicts:
                conflicts.setdefault(k.id, k)
        for k in facts.own_conflicts or []:
            conflicts.setdefault(k.id, k)

        out["target_degree"] = float(len(neigh))
        out["target_recent_change_count"] = float(sum(1 for c in changes.values() if A - RECENT_WINDOW < as_utc(c.at) <= A))  # type: ignore[arg-type]
        out["target_unique_agent_count"] = float(len({c.agent for c in changes.values() if c.agent}))
        out["target_active_experiment_count"] = float(len(active_exps))
        out["protected_targets_touched"] = float(sum(1 for t in targets if t.protected))
        out["changes_on_target_last_1h"] = float(sum(1 for x in times if A - H1 < x <= A))
        out["changes_on_target_last_24h"] = float(sum(1 for x in times if A - H24 < x <= A))
        out["seconds_since_last_target_change"] = float((A - max(times)).total_seconds()) if times else None
        out["conflicts_last_24h"] = float(sum(1 for k in conflicts.values() if k.at is not None and A - H24 < as_utc(k.at) <= A))
        if facts.path_checked:
            out["shortest_path_to_active_experiment"] = float(
                facts.path_hops if facts.path_hops is not None else UNREACHABLE_HOPS)
        if facts.agent_id:
            ids = {t.id for t in targets}
            out["agent_target_history_count"] = float(sum(
                1 for c in changes.values() if c.agent == facts.agent_id))
            if facts.agent_changes is not None:  # prefer the agent's own history (not limited to listed targets' edges)
                out["agent_target_history_count"] = float(sum(
                    1 for c in facts.agent_changes
                    if c.id != self_id and _le(c.at, A) and ids.intersection(c.target_ids)))

    # ---- changeset-centred
    if facts.own_conflicts is not None:
        own = {k.id: k for k in facts.own_conflicts if k.at is None or as_utc(k.at) <= A}
        out["changeset_conflict_degree"] = float(len(own))
        out["canonical_conflict_count"] = float(sum(1 for k in own.values() if _is_canonical(k.type)))
        out["duplicate_neighbor_count"] = float(len({
            o for k in own.values() if _is_duplicate(k.type) for o in k.others if o != self_id}))
    if facts.prompt_overlap is not None and facts.prompt_cluster_ids:
        seen = {cid for lst in facts.prompt_overlap.values() for cid, at in lst
                if cid != self_id and _le(at, A) and as_utc(at) > A - RECENT_WINDOW}  # type: ignore[arg-type]
        out["prompt_overlap_count"] = float(len(seen))
    if facts.changeset_found:
        out["dependency_depth"] = float(min(facts.dependency_depth or 0, MAX_DEPENDENCY_DEPTH))
        out["pending_predecessor_count"] = float(sum(
            1 for _id, st in facts.predecessors if (st or "").upper() not in TERMINAL_STATUSES))

    # ---- agent-centred
    if facts.agent_id and facts.agent_changes is not None:
        recent = {c.id: c for c in facts.agent_changes
                  if c.id != self_id and _le(c.at, A) and as_utc(c.at) > A - RECENT_WINDOW}  # type: ignore[arg-type]
        if recent:
            def _conflicted(c: ChangeFact) -> bool:
                return any(k.at is None or as_utc(k.at) <= A for k in c.conflicts)
            out["agent_recent_conflict_rate"] = sum(1 for c in recent.values() if _conflicted(c)) / len(recent)
        out["agent_changes_last_1h"] = float(sum(
            1 for c in facts.agent_changes if c.id != self_id and _le(c.at, A) and as_utc(c.at) > A - H1))  # type: ignore[arg-type]

    # ---- historical similar contexts (rates are None when there is no decided similar context)
    if facts.similar_checked:
        from app.graph.similarity import ALLOW_DECISIONS, BLOCK_DECISIONS, REVIEW_DECISIONS

        n = len(facts.similar_decisions)
        out["historical_similar_context_count"] = float(n)
        if n:
            ds = facts.similar_decisions
            out["historical_allow_rate"] = sum(d in ALLOW_DECISIONS for d in ds) / n
            out["historical_block_rate"] = sum(d in BLOCK_DECISIONS for d in ds) / n
            out["historical_review_rate"] = sum(d in REVIEW_DECISIONS for d in ds) / n
    return out


# ----------------------------------------------------------------------------------------------- encode
def _norm(value: float, kind: str, cap: float) -> float:
    v = max(0.0, float(value))
    if kind == "rate":
        return min(v, 1.0)
    if kind in ("hops", "depth"):
        return min(v / cap, 1.0)
    return min(math.log1p(v) / math.log1p(cap), 1.0)  # count | seconds


def encode_vector(values: dict[str, float | None]) -> list[float]:
    """Fixed order: 23 normalized values (0.0 when missing) then 23 missing flags. Rounded for hash stability."""
    vals: list[float] = []
    flags: list[float] = []
    for name, kind, cap in FEATURE_SPECS:
        v = values.get(name)
        if v is None:
            vals.append(0.0)
            flags.append(1.0)
        else:
            vals.append(round(_norm(v, kind, cap), 6))
            flags.append(0.0)
    return vals + flags


def context_hash(organization_id: str, as_of: datetime, values: dict[str, float | None],
                 context_key: dict[str, Any] | None = None, version: str = FEATURE_VERSION) -> str:
    payload = {
        "version": version, "organization_id": organization_id,
        "as_of": as_utc(as_of).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "context": context_key or {},
        "values": {n: (None if values.get(n) is None else round(float(values[n]), 6)) for n in FEATURE_NAMES},  # type: ignore[arg-type]
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
