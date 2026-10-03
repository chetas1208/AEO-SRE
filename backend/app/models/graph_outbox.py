"""Transactional outbox for the Neo4j projection (docs/GRAPH_SPEC.md).

A row is written in the SAME Postgres transaction as the domain mutation (ORM `after_flush` hook below + the explicit
`enqueue_graph_event` helper), so no event is lost and none exists for a rolled-back change. Rows are append-only
except the processing fields (processed_at, attempts, last_error, next_attempt_at, dead_at): ORM guard here, trigger in
migration 0006. `id` is deterministic (uuid5 of aggregate + type + version) and is also the Event node id.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, Uuid, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")
PROCESSING_FIELDS = frozenset({"processed_at", "attempts", "last_error", "next_attempt_at", "dead_at"})


class ImmutableOutboxError(Exception):
    """An outbox row's immutable fields were changed, or a row was deleted."""


class GraphOutbox(Base):
    __tablename__ = "graph_outbox"
    __table_args__ = (
        Index("ix_graph_outbox_created_at", "created_at"),
        Index("ix_graph_outbox_processed_at", "processed_at"),
        Index("ix_graph_outbox_org_created", "organization_id", "created_at"),
        Index("ix_graph_outbox_aggregate", "aggregate_type", "aggregate_id"),
        # the claim query: pending, not dead, due
        Index("ix_graph_outbox_pending", "next_attempt_at", "created_at",
              postgresql_where=text("processed_at IS NULL AND dead_at IS NULL"),
              sqlite_where=text("processed_at IS NULL AND dead_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # deterministic event id
    event_type: Mapped[str] = mapped_column(String(64))
    aggregate_type: Mapped[str] = mapped_column(String(48))
    aggregate_id: Mapped[str] = mapped_column(String(128))
    organization_id: Mapped[str] = mapped_column(String(64))  # org UUID, or "GLOBAL" for policy versions
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)  # projection plan (app/graph/events.py)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # dead-letter flag


@event.listens_for(GraphOutbox, "before_delete")
def _outbox_no_delete(mapper, connection, target) -> None:  # noqa: ARG001
    raise ImmutableOutboxError("graph_outbox rows are never deleted")


@event.listens_for(GraphOutbox, "before_update")
def _outbox_processing_only(mapper, connection, target) -> None:  # noqa: ARG001
    from sqlalchemy import inspect

    for attr in inspect(target).attrs:
        if attr.key not in PROCESSING_FIELDS and attr.history.has_changes():
            raise ImmutableOutboxError(f"graph_outbox.{attr.key} is immutable (only processing fields may change)")


def _after_flush(session: Session, flush_context) -> None:  # noqa: ARG001
    """Domain mutation -> outbox rows, inside the same transaction. Never lets the graph break a domain write."""
    from app.graph.events import on_after_flush

    on_after_flush(session)


event.listen(Session, "after_flush", _after_flush)
