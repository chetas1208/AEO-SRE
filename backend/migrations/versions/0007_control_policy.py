"""control policy: immutable versions, shadow/active decisions, feedback, offline evaluations

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03 23:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_DEC = "('ALLOW', 'MERGE', 'DELAY', 'REQUIRE_REVIEW', 'BLOCK')"
_TABLES = ('control_policy_versions', 'control_policy_decisions', 'control_policy_feedback',
           'control_policy_evaluations')


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        'control_policy_versions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
        sa.Column('algorithm', sa.String(length=48), nullable=False),
        sa.Column('feature_schema', sa.String(length=32), nullable=False),
        sa.Column('hyperparameters', _JSON, nullable=False),
        sa.Column('training_event_count', sa.Integer(), nullable=False),
        sa.Column('parent_version_id', sa.Uuid(), nullable=True),
        sa.Column('reward_config_version', sa.String(length=32), nullable=False),
        sa.Column('state_snapshot', _JSON, nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=False),
        _ts('created_at'),
        sa.ForeignKeyConstraint(['parent_version_id'], ['control_policy_versions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version'),
    )
    op.create_index('ix_control_policy_versions_parent_version_id', 'control_policy_versions', ['parent_version_id'])

    op.create_table(
        'control_policy_decisions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('change_set_id', sa.Uuid(), nullable=False),
        sa.Column('change_check_id', sa.Uuid(), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('policy_version_id', sa.Uuid(), nullable=True),
        sa.Column('algorithm', sa.String(length=48), nullable=True),
        sa.Column('baseline_decision', sa.String(length=16), nullable=False),
        sa.Column('recommended_action', sa.String(length=16), nullable=True),
        sa.Column('returned_decision', sa.String(length=16), nullable=False),
        sa.Column('eligible_actions', _JSON, nullable=False),
        sa.Column('masked_actions', _JSON, nullable=False),
        sa.Column('scores', _JSON, nullable=False),
        sa.Column('feature_schema', sa.String(length=32), nullable=True),
        sa.Column('feature_vector', _JSON, nullable=False),
        sa.Column('feature_names', _JSON, nullable=False),
        sa.Column('context_hash', sa.String(length=64), nullable=True),
        sa.Column('graph_feature_version', sa.String(length=32), nullable=True),
        sa.Column('graph_context_hash', sa.String(length=64), nullable=True),
        _ts('graph_snapshot_time', True),
        sa.Column('graph_source', sa.String(length=24), nullable=True),
        sa.Column('graph_stale', sa.Boolean(), nullable=False),
        sa.Column('graph_missing', _JSON, nullable=False),
        sa.Column('fallback_reason', sa.String(length=255), nullable=True),
        sa.Column('mask_violation', sa.Boolean(), nullable=False),
        sa.Column('agrees', sa.Boolean(), nullable=True),
        _ts('created_at'),
        sa.CheckConstraint("mode IN ('SHADOW', 'ACTIVE', 'BASELINE_ONLY')", name='ck_cpd_mode'),
        sa.CheckConstraint(f"baseline_decision IN {_DEC}", name='ck_cpd_baseline'),
        sa.CheckConstraint(f"returned_decision IN {_DEC}", name='ck_cpd_returned'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['change_set_id'], ['change_sets.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['change_check_id'], ['change_checks.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['policy_version_id'], ['control_policy_versions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('change_check_id', 'mode', name='uq_cpd_check_mode'),
    )
    op.create_index('ix_control_policy_decisions_org_id', 'control_policy_decisions', ['org_id'])
    op.create_index('ix_control_policy_decisions_change_set_id', 'control_policy_decisions', ['change_set_id'])
    op.create_index('ix_control_policy_decisions_policy_version_id', 'control_policy_decisions', ['policy_version_id'])
    op.create_index('ix_cpd_org_created', 'control_policy_decisions', ['org_id', 'created_at'])

    op.create_table(
        'control_policy_feedback',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('decision_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('policy_recommendation', sa.String(length=16), nullable=True),
        sa.Column('human_decision', sa.String(length=16), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('final_execution', sa.String(length=16), nullable=True),
        sa.Column('eventual_outcome', sa.String(length=48), nullable=True),
        sa.Column('components', _JSON, nullable=False),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('settled', sa.Boolean(), nullable=False),
        sa.Column('reward_config_version', sa.String(length=32), nullable=False),
        sa.Column('actor', sa.String(length=255), nullable=False),
        _ts('decision_created_at', True),
        _ts('outcome_at', True),
        sa.Column('delay_seconds', sa.Float(), nullable=True),
        _ts('recorded_at'),
        sa.CheckConstraint("kind IN ('human', 'outcome')", name='ck_cpf_kind'),
        sa.ForeignKeyConstraint(['decision_id'], ['control_policy_decisions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
        sa.UniqueConstraint('decision_id', 'kind', name='uq_cpf_decision_kind'),
    )
    op.create_index('ix_control_policy_feedback_decision_id', 'control_policy_feedback', ['decision_id'])
    op.create_index('uq_cpf_one_settled', 'control_policy_feedback', ['decision_id'], unique=True,
                    postgresql_where=sa.text('settled'), sqlite_where=sa.text('settled'))

    op.create_table(
        'control_policy_evaluations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('policy_version_id', sa.Uuid(), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('data_kind', sa.String(length=24), nullable=False),
        sa.Column('metrics', _JSON, nullable=False),
        sa.Column('report_ref', sa.String(length=255), nullable=True),
        _ts('created_at'),
        sa.ForeignKeyConstraint(['policy_version_id'], ['control_policy_versions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_control_policy_evaluations_policy_version_id', 'control_policy_evaluations',
                    ['policy_version_id'])

    if op.get_bind().dialect.name == 'postgresql':
        # append-only / immutable (TRUNCATE is not blocked: test isolation / ops)
        op.execute("""
            CREATE FUNCTION control_policy_immutable() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION '% rows are append-only / immutable', TG_TABLE_NAME;
            END $$ LANGUAGE plpgsql
        """)
        for t in _TABLES:
            op.execute(f'CREATE TRIGGER trg_{t}_immutable BEFORE UPDATE OR DELETE ON {t} '
                       'FOR EACH ROW EXECUTE FUNCTION control_policy_immutable()')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for t in reversed(_TABLES):
            op.execute(f'DROP TRIGGER IF EXISTS trg_{t}_immutable ON {t}')
        op.execute('DROP FUNCTION IF EXISTS control_policy_immutable()')
    op.drop_table('control_policy_evaluations')
    op.drop_table('control_policy_feedback')
    op.drop_table('control_policy_decisions')
    op.drop_table('control_policy_versions')
