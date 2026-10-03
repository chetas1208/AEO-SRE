"""Typed results returned by the graph query layer (consumed by N5 policy and N6 API)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str  # business id (Postgres UUID / event id); never a Neo4j internal id
    label: str
    props: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str  # deterministic: "<source>|<TYPE>|<target>"
    source: str
    target: str
    type: str


class GraphView(BaseModel):
    """Normalized view for the 3D topology: frontend knows no Cypher."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    focus_id: str | None = None
    generated_at: datetime
    as_of: datetime | None = None
    max_hops: int = 0
    truncated: bool = False
    found: bool = True


class ExplanationStatement(BaseModel):
    text: str
    node_ids: list[str] = Field(default_factory=list)
    edges: list[str] = Field(default_factory=list)  # "<src>|<TYPE>|<dst>" refs, all real


class DecisionExplanation(BaseModel):
    changeset_id: str
    found: bool = True
    decision: str | None = None
    decision_id: str | None = None
    text: str = ""
    statements: list[ExplanationStatement] = Field(default_factory=list)
    generated_at: datetime
    as_of: datetime | None = None


class SimilarContext(BaseModel):
    changeset_id: str
    decision: str | None
    decision_id: str | None = None
    score: float
    matched: dict[str, float] = Field(default_factory=dict)  # dimension -> similarity in [0, 1]
    occurred_at: datetime | None = None
    outcome: str | None = None  # outcome label if one was measured by as_of
    reward: float | None = None


class DecisionStats(BaseModel):
    decision: str
    n: int
    with_outcome: int = 0
    mean_reward: float | None = None
    positive_rate: float | None = None


class SimilarContexts(BaseModel):
    changeset_id: str | None
    contexts: list[SimilarContext] = Field(default_factory=list)
    candidates_considered: int = 0
    by_decision: list[DecisionStats] = Field(default_factory=list)
    allow_rate: float | None = None
    block_rate: float | None = None
    review_rate: float | None = None
    generated_at: datetime
    as_of: datetime | None = None
    truncated: bool = False


class AgentConflictPair(BaseModel):
    agent_a: str
    agent_b: str
    conflict_count: int
    conflict_types: dict[str, int] = Field(default_factory=dict)
    targets: list[str] = Field(default_factory=list)
    last_at: datetime | None = None
    conflict_ids: list[str] = Field(default_factory=list)


class AgentConflicts(BaseModel):
    pairs: list[AgentConflictPair] = Field(default_factory=list)
    generated_at: datetime
    since: datetime | None = None
    as_of: datetime | None = None
    truncated: bool = False


class TargetContentionRow(BaseModel):
    target_id: str
    target_key: str | None = None
    protected: bool = False
    change_count: int = 0
    agent_count: int = 0
    conflict_count: int = 0
    active_experiment_count: int = 0
    score: float = 0.0  # change_count + 2*conflict_count + 3*active_experiment_count


class TargetContention(BaseModel):
    rows: list[TargetContentionRow] = Field(default_factory=list)
    generated_at: datetime
    since: datetime | None = None
    as_of: datetime | None = None
    truncated: bool = False


class ContradictedClaim(BaseModel):
    claim_id: str
    claim_text: str | None = None
    claim_type: str | None = None
    conflict_count: int
    changeset_count: int
    agent_count: int
    last_at: datetime | None = None
    conflict_ids: list[str] = Field(default_factory=list)


class ContradictedClaims(BaseModel):
    claims: list[ContradictedClaim] = Field(default_factory=list)
    generated_at: datetime
    since: datetime | None = None
    as_of: datetime | None = None
    min_count: int = 2
    truncated: bool = False


class GraphFeatures(BaseModel):
    """Result handed to the N5 policy. Never raised on outage: `stale`/`unavailable` signal baseline fallback."""

    vector: list[float] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    version: str = "graph_context_v1"
    snapshot_time: datetime | None = None  # the as_of the features were computed against
    computed_at: datetime | None = None
    context_hash: str | None = None
    stale: bool = True
    unavailable: bool = False
    source: str = "graph"  # graph | cache | unavailable
    reason: str | None = None
    features: dict[str, float] = Field(default_factory=dict)  # raw (un-normalized) values, 0.0 where missing
    missing: list[str] = Field(default_factory=list)  # feature names whose value is unknown
    organization_id: str | None = None
    changeset_id: str | None = None

    @property
    def usable(self) -> bool:
        return not self.stale and not self.unavailable


class ChangeContext(BaseModel):
    """Explicit description of a (possibly not-yet-projected) change. Any field left empty is filled from the graph
    when `changeset_id` resolves to a projected ChangeSet; explicit values win."""

    changeset_id: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    action_type: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    target_keys: list[str] = Field(default_factory=list)
    target_type: str | None = None
    claim_types: list[str] = Field(default_factory=list)
    prompt_cluster_ids: list[str] = Field(default_factory=list)
    conflict_types: list[str] = Field(default_factory=list)
    experiment_ids: list[str] = Field(default_factory=list)

    def key(self) -> str:
        d = self.model_dump()
        for k, v in d.items():
            if isinstance(v, list):
                d[k] = sorted(set(map(str, v)))
        import json

        return json.dumps(d, sort_keys=True, separators=(",", ":"))
