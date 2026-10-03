"""Response schemas for the evidence ledger and per-incident evidence graph.

Wire format is snake_case (API contract). Every field also has a camelCase alias matching UI.md §51, so
`model_dump(by_alias=True)` yields the frontend shape and either spelling is accepted on input.
"""
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.domain.enums import EdgeType, EvidenceStatus, EvidenceType, HypothesisStatus
from app.evidence.provenance import domain_of


class NodeClass(StrEnum):
    PROFOUND = "profound"
    OWNED = "owned"
    COMPETITOR = "competitor"
    EXTERNAL = "external"
    INFERENCE = "inference"
    PROMPT_CLUSTER = "prompt_cluster"
    EXPERIMENT = "experiment"


class NodeRole(StrEnum):
    ROOT = "root"
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"
    PROMPT_CLUSTER = "prompt_cluster"
    EXPERIMENT = "experiment"


class _Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)


class ProvenanceOut(_Schema):
    source: str | None = None
    timestamp: datetime | str | None = None
    confidence: float | None = None
    extract: str | None = None
    hash: str | None = None
    retrieval_method: str | None = None
    missing: list[str] = Field(default_factory=list, description="provenance fields that were not recorded")


class EvidenceItem(_Schema):
    id: uuid.UUID
    type: EvidenceType
    title: str
    source: str | None = None
    domain: str | None = None
    url: str | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    support_score: float | None = None
    contradiction_score: float | None = None
    insufficient_score: float | None = None
    freshness_risk: float | None = None
    confidence: float | None = None
    status: EvidenceStatus
    content_hash: str | None = None
    retrieval_method: str | None = None

    @model_validator(mode="after")
    def _derive_domain(self):
        if self.domain is None and self.url:
            self.domain = domain_of(self.url)
        return self


class EvidenceDetail(EvidenceItem):
    incident_id: uuid.UUID
    excerpt: str | None = Field(None, description="verbatim source extract; never a model paraphrase")
    raw: dict[str, Any] = Field(default_factory=dict)
    provenance: ProvenanceOut | None = None
    edges_in: list["GraphEdge"] = Field(default_factory=list)
    edges_out: list["GraphEdge"] = Field(default_factory=list)


class HypothesisOut(_Schema):
    id: uuid.UUID
    title: str
    summary: str = ""
    confidence: float | None = None
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    contradicting_evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    rationale: str = ""
    produced_by: str = "rules"


class GraphNode(_Schema):
    id: str
    node_class: NodeClass
    role: NodeRole
    title: str
    level: int = 0
    status: EvidenceStatus | HypothesisStatus | None = None
    evidence_id: uuid.UUID | None = None
    hypothesis_id: uuid.UUID | None = None
    source: str | None = None
    url: str | None = None
    observed_at: datetime | None = None
    confidence: float | None = None
    extract: str | None = None
    provenance: ProvenanceOut
    data: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(_Schema):
    id: str
    source: str
    target: str
    type: EdgeType
    confidence: float | None = None
    provenance: ProvenanceOut
    conflict: bool = Field(False, description="True for contradicts edges (preserved, never dropped)")
    derived: bool = Field(False, description="not persisted as an edge row; derived from Hypothesis.evidence_ids")
    back_edge: bool = Field(False, description="closes a cycle; kept for audit, excluded from level layout")


class GraphValidationOut(_Schema):
    is_acyclic: bool = True
    cycles: list[list[str]] = Field(default_factory=list)
    dangling_edges: list[dict[str, Any]] = Field(default_factory=list)
    nodes_missing_provenance: list[str] = Field(default_factory=list)
    edges_missing_provenance: list[str] = Field(default_factory=list)
    disconnected_nodes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GraphOut(_Schema):
    incident_id: uuid.UUID
    root_id: str | None = None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    levels: list[list[str]] = Field(default_factory=list)
    topological_order: list[str] = Field(default_factory=list)
    validation: GraphValidationOut = Field(default_factory=GraphValidationOut)
    counts: dict[str, int] = Field(default_factory=dict)


EvidenceDetail.model_rebuild()
