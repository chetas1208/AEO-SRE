"""Live Mixpanel telemetry events + ingestion cursors.

Revision ID: 0010
Revises: 0009
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "live_telemetry_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_event", sa.String(length=255), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_resolution", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=128), nullable=True),
        sa.Column("agent_id", sa.String(length=128), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.String(length=128), nullable=True),
        sa.Column("properties", _JSON, nullable=False),
        sa.Column("correlation_keys", _JSON, nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("correlation_method", sa.String(length=16), nullable=False),
        sa.Column("correlation_confidence", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_live_telemetry_events_source_event", "live_telemetry_events", ["source_event"], unique=False)
    op.create_index("ix_live_telemetry_events_occurred_at", "live_telemetry_events", ["occurred_at"], unique=False)
    op.create_index("ix_telemetry_org_occurred", "live_telemetry_events", ["organization_id", "occurred_at"], unique=False)
    op.create_index(
        "uq_telemetry_source_event", "live_telemetry_events", ["source", "source_event_id"], unique=True
    )
    op.create_index("ix_live_telemetry_events_campaign_id", "live_telemetry_events", ["campaign_id"], unique=False)
    op.create_index("ix_live_telemetry_events_experiment_id", "live_telemetry_events", ["experiment_id"], unique=False)
    op.create_index(
        "ix_live_telemetry_events_organization_id", "live_telemetry_events", ["organization_id"], unique=False
    )

    op.create_table(
        "mixpanel_cursors",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("last_successful_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_id", sa.String(length=128), nullable=True),
        sa.Column("last_query_window", _JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("org_id"),
    )


def downgrade() -> None:
    op.drop_table("mixpanel_cursors")
    op.drop_table("live_telemetry_events")
