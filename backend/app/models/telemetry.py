"""Live behavioral telemetry (Mixpanel and future sources). Postgres = observation store; not domain truth."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class LiveTelemetryEvent(TimestampMixin, Base):
    __tablename__ = "live_telemetry_events"
    __table_args__ = (
        Index("ix_telemetry_org_occurred", "organization_id", "occurred_at"),
        Index("uq_telemetry_source_event", "source", "source_event_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), default="MIXPANEL")
    source_event: Mapped[str] = mapped_column(String(255), index=True)
    source_event_id: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tenant_resolution: Mapped[str] = mapped_column(String(16), default="UNRESOLVED")
    campaign_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    properties: Mapped[dict] = mapped_column(JSONType, default=dict)
    correlation_keys: Mapped[dict] = mapped_column(JSONType, default=dict)
    raw_hash: Mapped[str] = mapped_column(String(64), default="")
    correlation_method: Mapped[str] = mapped_column(String(16), default="UNRESOLVED")
    correlation_confidence: Mapped[str] = mapped_column(String(8), default="LOW")


class MixpanelCursor(Base):
    __tablename__ = "mixpanel_cursors"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    last_successful_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_query_window: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
