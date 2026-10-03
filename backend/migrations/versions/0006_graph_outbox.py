"""graph outbox (Neo4j projection)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03 22:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'graph_outbox',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('aggregate_type', sa.String(length=48), nullable=False),
        sa.Column('aggregate_id', sa.String(length=128), nullable=False),
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('payload', _JSON, nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('dead_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_graph_outbox_created_at', 'graph_outbox', ['created_at'])
    op.create_index('ix_graph_outbox_processed_at', 'graph_outbox', ['processed_at'])
    op.create_index('ix_graph_outbox_org_created', 'graph_outbox', ['organization_id', 'created_at'])
    op.create_index('ix_graph_outbox_aggregate', 'graph_outbox', ['aggregate_type', 'aggregate_id'])
    op.create_index('ix_graph_outbox_pending', 'graph_outbox', ['next_attempt_at', 'created_at'],
                    postgresql_where=sa.text('processed_at IS NULL AND dead_at IS NULL'))
    if op.get_bind().dialect.name == 'postgresql':
        # append-only except the processing fields; never deleted (TRUNCATE is not blocked: test isolation / ops)
        op.execute("""
            CREATE FUNCTION graph_outbox_guard() RETURNS trigger AS $$
            BEGIN
              IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'graph_outbox rows are never deleted';
              END IF;
              IF NEW.id IS DISTINCT FROM OLD.id OR NEW.event_type IS DISTINCT FROM OLD.event_type
                 OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
                 OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
                 OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
                 OR NEW.payload IS DISTINCT FROM OLD.payload
                 OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
                 OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'graph_outbox: only processing fields may change';
              END IF;
              RETURN NEW;
            END $$ LANGUAGE plpgsql
        """)
        op.execute('CREATE TRIGGER trg_graph_outbox_guard BEFORE UPDATE OR DELETE ON graph_outbox '
                   'FOR EACH ROW EXECUTE FUNCTION graph_outbox_guard()')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER IF EXISTS trg_graph_outbox_guard ON graph_outbox')
        op.execute('DROP FUNCTION IF EXISTS graph_outbox_guard()')
    op.drop_table('graph_outbox')
