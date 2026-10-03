"""Evidence ledger tables: Evidence, EvidenceNode (synthetic graph nodes), EvidenceEdge, Hypothesis.

The per-incident evidence graph is built exclusively from these rows (plus the Incident row itself).
Edge endpoints are polymorphic UUIDs: Evidence.id | EvidenceNode.id | Hypothesis.id | Incident.id.
Integrity of endpoints is enforced by `app.evidence.graph.build_graph`, not by FK constraints.
"""
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text, Uuid, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.domain.enums import EdgeType, EvidenceStatus, EvidenceType, HypothesisStatus

JSONType = JSON().with_variant(JSONB(), "postgresql")


class NodeKind(StrEnum):
    """Kinds of synthetic (non-evidence) graph nodes persisted in `evidence_nodes`."""

    ROOT = "root"                      # the incident symptom, e.g. "Visibility Loss -24pp"
    PROMPT_CLUSTER = "prompt_cluster"
    EXPERIMENT = "experiment"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Evidence(TimestampMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_incident_type", "incident_id", "type"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), default=EvidenceType.EXTERNAL.value)
    status: Mapped[str] = mapped_column(String(16), default=EvidenceStatus.LIVE.value)
    title: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    support_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    contradiction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    insufficient_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    freshness_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)


class EvidenceNode(TimestampMixin, Base):
    """Synthetic node that is not itself a piece of evidence (root symptom, prompt cluster, experiment)."""

    __tablename__ = "evidence_nodes"
    __table_args__ = (Index("ix_evidence_nodes_incident_kind", "incident_id", "kind"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. prompt_clusters, experiments
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)


class EvidenceEdge(TimestampMixin, Base):
    __tablename__ = "evidence_edges"
    __table_args__ = (Index("ix_evidence_edges_incident_src_dst", "incident_id", "src_id", "dst_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    src_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    dst_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    edge_type: Mapped[str] = mapped_column(String(32), default=EdgeType.ASSOCIATED_WITH.value)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # keys: source, timestamp, confidence, extract, hash, retrieval_method
    provenance: Mapped[dict] = mapped_column(JSONType, default=dict)


class Hypothesis(TimestampMixin, Base):
    __tablename__ = "hypotheses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=HypothesisStatus.PROPOSED.value)  # proposed != fact
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_ids: Mapped[list] = mapped_column(JSONType, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    produced_by: Mapped[str] = mapped_column(String(128), default="rules")


class IllegalHypothesisTransition(Exception):
    pass


_HYP_ALLOWED = {
    HypothesisStatus.PROPOSED.value: {HypothesisStatus.CONFIRMED.value, HypothesisStatus.REJECTED.value},
    HypothesisStatus.CONFIRMED.value: {HypothesisStatus.REJECTED.value},
    HypothesisStatus.REJECTED.value: set(),
}


@event.listens_for(Hypothesis, "before_update")
def _hypothesis_guard(mapper, connection, target: Hypothesis) -> None:
    """proposed -> confirmed|rejected, confirmed -> rejected; a confirmation must cite at least one evidence id."""
    from sqlalchemy import inspect as _inspect

    hist = _inspect(target).attrs.status.history
    if not hist.has_changes() or not hist.deleted or not hist.added:
        return
    old, new = str(hist.deleted[0]), str(hist.added[0])
    if old == new:
        return
    if new not in _HYP_ALLOWED.get(old, set()):
        raise IllegalHypothesisTransition(f"hypothesis {target.id}: {old} -> {new} is not allowed")
    if new == HypothesisStatus.CONFIRMED.value and not (target.evidence_ids or []):
        raise IllegalHypothesisTransition(f"hypothesis {target.id}: cannot be confirmed without evidence")
