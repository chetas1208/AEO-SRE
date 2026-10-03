"""Response models for /api/graph/*. snake_case. Every read view carries `generated_at`, `source` and `graph_stale`;
when Neo4j cannot answer, `source = "unavailable"`, collections are empty and `reason` says why (never fake data)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GraphSource = Literal["neo4j", "unavailable"]
Since = Literal["1h", "24h", "7d"]


class GraphMeta(BaseModel):
    organization_id: str
    generated_at: datetime
    source: GraphSource
    graph_stale: bool = False  # projection lag > GRAPH_STALE_SECONDS (or unknown): data may miss recent changes
    reason: str | None = None  # why source is "unavailable" / what is degraded (stable machine-ish string)


class NodeOut(BaseModel):
    id: str  # business id (Postgres UUID / event id / org-namespaced key); never a Neo4j internal id
    label: str
    props: dict[str, Any] = Field(default_factory=dict)


class EdgeOut(BaseModel):
    id: str  # "<source>|<TYPE>|<target>"
    source: str
    target: str
    type: str


class HighlightPaths(BaseModel):
    """Arrays of REAL node ids found in this view (empty when the chain is not present). Never inferred."""

    decision_id: str | None = None
    decision_path: list[str] = Field(default_factory=list)  # Agent -> Run -> Event -> Change -> Conflict -> Decision
    outcome_path: list[str] = Field(default_factory=list)  # Decision -> Execution -> Observation -> Outcome -> Policy update
    decision_path_labels: list[str] = Field(default_factory=list)
    outcome_path_labels: list[str] = Field(default_factory=list)


class LineageResponse(GraphMeta):
    nodes: list[NodeOut] = Field(default_factory=list)
    edges: list[EdgeOut] = Field(default_factory=list)
    focus_id: str | None = None
    max_hops: int = 0
    truncated: bool = False
    found: bool = False  # False: the focus is not (yet) in the graph
    as_of: datetime | None = None
    highlight_paths: HighlightPaths = Field(default_factory=HighlightPaths)


class StatementOut(BaseModel):
    text: str
    node_ids: list[str]  # supporting nodes (never empty)
    edge_ids: list[str]  # supporting edges "<src>|<TYPE>|<dst>"


class ExplanationResponse(GraphMeta):
    changeset_id: str
    found: bool = False
    decision: str | None = None
    decision_id: str | None = None
    text: str = ""
    statements: list[StatementOut] = Field(default_factory=list)


class SimilarContextOut(BaseModel):
    changeset_id: str
    decision: str | None = None
    decision_id: str | None = None
    score: float
    matched: dict[str, float] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    outcome: str | None = None
    reward: float | None = None


class DecisionStatsOut(BaseModel):
    decision: str
    n: int
    with_outcome: int = 0
    mean_reward: float | None = None
    positive_rate: float | None = None


class ContextResponse(GraphMeta):
    changeset_id: str
    version: str = "graph_context_v1"
    snapshot_time: datetime | None = None
    stale: bool = True  # features unusable for the policy (stale or unavailable): BaselinePolicy applies
    names: list[str] = Field(default_factory=list)
    vector: list[float] = Field(default_factory=list)
    features: dict[str, float] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    context_hash: str | None = None
    similar_contexts: list[SimilarContextOut] = Field(default_factory=list)
    similar_by_decision: list[DecisionStatsOut] = Field(default_factory=list)
    candidates_considered: int = 0


class AgentConflictPairOut(BaseModel):
    agent_a: str
    agent_b: str
    conflict_count: int
    conflict_types: dict[str, int] = Field(default_factory=dict)
    targets: list[str] = Field(default_factory=list)
    last_at: datetime | None = None
    conflict_ids: list[str] = Field(default_factory=list)


class AgentConflictsResponse(GraphMeta):
    agent_id: str | None = None
    since: Since | None = None
    pairs: list[AgentConflictPairOut] = Field(default_factory=list)
    truncated: bool = False


class TargetContentionRowOut(BaseModel):
    target_id: str
    target_key: str | None = None
    protected: bool = False
    change_count: int = 0
    agent_count: int = 0
    conflict_count: int = 0
    active_experiment_count: int = 0
    score: float = 0.0


class TargetContentionResponse(GraphMeta):
    since: Since | None = None
    limit: int
    rows: list[TargetContentionRowOut] = Field(default_factory=list)
    truncated: bool = False


class ContradictedClaimOut(BaseModel):
    claim_id: str
    claim_text: str | None = None
    claim_type: str | None = None
    conflict_count: int
    changeset_count: int
    agent_count: int
    last_at: datetime | None = None
    conflict_ids: list[str] = Field(default_factory=list)


class ContradictedClaimsResponse(GraphMeta):
    since: Since | None = None
    min_count: int
    limit: int
    claims: list[ContradictedClaimOut] = Field(default_factory=list)
    truncated: bool = False


class ProjectionLag(BaseModel):
    backlog: int = 0
    oldest_unprocessed_age_s: float | None = None
    dead_lettered: int = 0
    stale: bool = True
    stale_threshold_s: int | None = None
    last_error: str | None = None
    checkpoint: dict[str, Any] | None = None


class LastProjectedEvent(BaseModel):
    event_id: str
    event_type: str
    organization_id: str
    processed_at: datetime | None = None


class GraphHealthResponse(BaseModel):
    generated_at: datetime
    source: GraphSource
    graph_stale: bool
    reason: str | None = None
    organization_id: str | None = None
    neo4j_state: Literal["NOT_CONFIGURED", "READY", "DEGRADED", "AUTH_FAILED"]
    neo4j_latency_ms: float | None = None
    neo4j_error: str | None = None
    database: str | None = None
    projection: ProjectionLag = Field(default_factory=ProjectionLag)
    last_projected_event: LastProjectedEvent | None = None
    node_counts: dict[str, int] = Field(default_factory=dict)  # only with org_id and a READY graph


class GraphProjectedEvent(BaseModel):
    """Payload of the `graph_projected` SSE event on GET /api/events (documented here for the OpenAPI/contract)."""

    type: Literal["graph_projected"] = "graph_projected"
    organization_id: str
    projected: int  # outbox rows projected since the previous event (coalesced)
    last_event_id: str | None = None
    last_event_type: str | None = None
    backlog: int = 0
    time: datetime
