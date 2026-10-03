"""Core domain tables: organizations, signals, incidents, prompt clusters, jobs, audit, incident events."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    Uuid,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow
from app.domain.enums import IncidentCategory, IncidentState, Severity, StepStatus

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    competitor_domains: Mapped[list] = mapped_column(JSONType, default=list)
    canonical_domains: Mapped[list] = mapped_column(JSONType, default=list)
    personas: Mapped[list] = mapped_column(JSONType, default=list)
    topics: Mapped[list] = mapped_column(JSONType, default=list)


class PromptCluster(TimestampMixin, Base):
    __tablename__ = "prompt_clusters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(255), index=True)
    prompts: Mapped[list] = mapped_column(JSONType, default=list)


class Signal(TimestampMixin, Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_org_kind_observed", "org_id", "kind", "observed_at"),
        # One logical signal per (org, source, idempotency key); NULL keys (hand-built rows) never collide.
        Index("uq_signals_org_source_idem", "org_id", "source", "idempotency_key", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(128), default="profound")
    metric: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    prompt_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompt_clusters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)
    raw_payload_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Stable provider run/report id when the provider gives one; filled from raw["provider_run_id"].
    provider_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Provider-specific idempotency key (ingestion's dedupe_key unless a stable run id exists). Never NULL for ingested rows.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


@event.listens_for(Signal, "before_insert")
def _signal_idempotency(mapper, connection, target: "Signal") -> None:
    raw = target.raw or {}
    if target.provider_run_id is None and raw.get("provider_run_id"):
        target.provider_run_id = str(raw["provider_run_id"])[:128]
    if target.idempotency_key is None:
        key = raw.get("dedupe_key")
        if key and target.provider_run_id:
            key = f"run:{target.provider_run_id}:{key}"
        if key:
            target.idempotency_key = str(key)[:128]


incident_number_seq = Sequence("incident_number_seq", metadata=Base.metadata)
incident_event_seq = Sequence("incident_event_seq", metadata=Base.metadata)


_TERMINAL_INCIDENT_SQL = "state NOT IN ('closed', 'dismissed', 'failed')"


class Incident(TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        # DB backstop for dedup: at most one NON-TERMINAL incident per deterministic fingerprint.
        Index(
            "uq_incidents_open_fingerprint", "fingerprint", unique=True,
            postgresql_where=text(_TERMINAL_INCIDENT_SQL), sqlite_where=text(_TERMINAL_INCIDENT_SQL),
        ),
        Index("ix_incidents_org_state", "org_id", "state"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(64), default=IncidentCategory.VISIBILITY_DROP.value)
    severity: Mapped[str] = mapped_column(String(16), default=Severity.MEDIUM.value, index=True)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    priority_breakdown: Mapped[dict] = mapped_column(JSONType, default=dict)
    state: Mapped[str] = mapped_column(String(32), default=IncidentState.DETECTED.value, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prompt_cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompt_clusters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    metrics: Mapped[list] = mapped_column(JSONType, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    investigation_status: Mapped[str] = mapped_column(String(32), default="not_started")
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    # sha256 over (org, type, topic/cluster, persona, platform, competitor/source, time bucket); see incidents/fingerprint.py
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


@event.listens_for(Incident, "before_insert")
def _incident_fingerprint(mapper, connection, target: "Incident") -> None:
    # Only detector-created incidents (they carry a detection signature) get a dedup fingerprint; hand-built
    # rows stay NULL so they are never falsely collapsed.
    if target.fingerprint is None and (target.context or {}).get("signature"):
        from app.incidents.fingerprint import incident_fingerprint

        target.fingerprint = incident_fingerprint(target)


@event.listens_for(Incident, "before_update")
def _incident_state_guard(mapper, connection, target: "Incident") -> None:
    """No arbitrary status assignment: any persisted state change must be an edge of the incident state machine."""
    from sqlalchemy import inspect as _inspect

    hist = _inspect(target).attrs.state.history
    if not hist.has_changes() or not hist.deleted or not hist.added:
        return
    old, new = hist.deleted[0], hist.added[0]
    if str(getattr(old, "value", old)) == str(getattr(new, "value", new)):
        return
    from app.incidents.state_machine import IllegalTransition, can_transition

    if not can_transition(old, new):
        raise IllegalTransition(old, new, f"incident {target.id}: {old} -> {new} is not a state-machine edge")


class IncidentEvent(Base):
    """Persisted SSE log for an incident (investigation timeline)."""

    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident_seq", "incident_id", "seq"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    seq: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=StepStatus.RUNNING.value)
    message: Mapped[str] = mapped_column(Text, default="")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True
    )  # queued|running|success|failed
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_type: Mapped[str] = mapped_column(String(16))  # system|human|model
    actor: Mapped[str] = mapped_column(String(255))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    event: Mapped[str] = mapped_column(String(128))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ImmutableAuditError(Exception):
    """Audit events are append-only."""


@event.listens_for(AuditEvent, "before_update")
def _audit_no_update(mapper, connection, target):
    raise ImmutableAuditError("audit events are append-only")


@event.listens_for(AuditEvent, "before_delete")
def _audit_no_delete(mapper, connection, target):
    raise ImmutableAuditError("audit events are append-only")


def _fill_counter(model, column: str, seq: Sequence):
    """Assign the counter before insert so the value is loaded on the instance (async-safe).
    Postgres uses the sequence; elsewhere (sqlite tests) max+1."""

    def _listener(mapper, connection, target):
        if getattr(target, column) is not None:
            return
        if connection.dialect.name == "postgresql":
            value = connection.execute(seq.next_value()).scalar_one()
        else:
            # A batched INSERT (insertmanyvalues) fires every before_insert before any row exists, so max+1 alone
            # would hand the same number to each row of one flush; remember what this connection already issued.
            issued = connection.info.setdefault("_counter_issued", {})
            key = (model.__tablename__, column)
            db_max = connection.execute(select(func.max(getattr(model, column)))).scalar() or 0
            value = max(db_max, issued.get(key, 0)) + 1
            issued[key] = value
        setattr(target, column, value)

    event.listen(model, "before_insert", _listener)


_fill_counter(Incident, "number", incident_number_seq)
_fill_counter(IncidentEvent, "seq", incident_event_seq)


class Setting(TimestampMixin, Base):
    """Operator-editable key/value settings (policy toggles etc.). Never stores secrets."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
