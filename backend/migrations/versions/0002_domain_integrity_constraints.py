"""domain integrity constraints

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03 08:56:58.735291
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('experiment_outcomes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('experiment_id', sa.Uuid(), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('observe_outcome', sa.String(length=24), nullable=True),
    sa.Column('reward_total', sa.Float(), nullable=True),
    sa.Column('components', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('confounders', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('causal_confidence', sa.String(length=16), nullable=False),
    sa.Column('learning_applied', sa.Boolean(), nullable=False),
    sa.Column('policy_version_id', sa.Uuid(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('methodology', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['policy_version_id'], ['policy_versions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_experiment_outcomes_experiment_id'), 'experiment_outcomes', ['experiment_id'], unique=True)
    op.create_index('uq_approvals_one_pending', 'approvals', ['intervention_id'], unique=True, postgresql_where=sa.text("status = 'pending'"), sqlite_where=sa.text("status = 'pending'"))
    op.drop_constraint(op.f('approvals_intervention_id_fkey'), 'approvals', type_='foreignkey')
    op.create_foreign_key('approvals_intervention_id_fkey', 'approvals', 'interventions', ['intervention_id'], ['id'], ondelete='RESTRICT')
    op.drop_constraint(op.f('executions_intervention_id_fkey'), 'executions', type_='foreignkey')
    op.create_foreign_key('executions_intervention_id_fkey', 'executions', 'interventions', ['intervention_id'], ['id'], ondelete='RESTRICT')
    op.add_column('experiments', sa.Column('spec', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('experiments', sa.Column('policy_decision_id', sa.Uuid(), nullable=True))
    op.add_column('experiments', sa.Column('policy_action', sa.String(length=48), nullable=True))
    op.add_column('experiments', sa.Column('override_reason', sa.Text(), nullable=True))
    op.add_column('experiments', sa.Column('override_by', sa.String(length=255), nullable=True))
    op.add_column('experiments', sa.Column('target_key', sa.String(length=512), nullable=True))
    op.create_index(op.f('ix_experiments_policy_decision_id'), 'experiments', ['policy_decision_id'], unique=False)
    op.create_index(op.f('ix_experiments_target_key'), 'experiments', ['target_key'], unique=False)
    op.drop_constraint(op.f('experiments_intervention_id_fkey'), 'experiments', type_='foreignkey')
    op.drop_constraint(op.f('experiments_incident_id_fkey'), 'experiments', type_='foreignkey')
    op.create_foreign_key('experiments_policy_decision_id_fkey', 'experiments', 'policy_decisions', ['policy_decision_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('experiments_intervention_id_fkey', 'experiments', 'interventions', ['intervention_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('experiments_incident_id_fkey', 'experiments', 'incidents', ['incident_id'], ['id'], ondelete='RESTRICT')
    op.add_column('incidents', sa.Column('fingerprint', sa.String(length=64), nullable=True))
    op.create_index('ix_incidents_org_state', 'incidents', ['org_id', 'state'], unique=False)
    op.create_index('uq_incidents_open_fingerprint', 'incidents', ['fingerprint'], unique=True, postgresql_where=sa.text("state NOT IN ('closed', 'dismissed', 'failed')"), sqlite_where=sa.text("state NOT IN ('closed', 'dismissed', 'failed')"))
    op.add_column('observations', sa.Column('source_run_id', sa.String(length=128), server_default='', nullable=False))
    op.create_unique_constraint('uq_observations_source_snapshot', 'observations', ['experiment_id', 'source', 'source_run_id', 'observed_at'])
    op.drop_constraint(op.f('observations_experiment_id_fkey'), 'observations', type_='foreignkey')
    op.create_foreign_key('observations_experiment_id_fkey', 'observations', 'experiments', ['experiment_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('policy_decisions_incident_id_fkey', 'policy_decisions', 'incidents', ['incident_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_policy_versions_source_experiment', 'policy_versions', 'experiments', ['source_experiment_id'], ['id'], ondelete='RESTRICT')
    op.drop_constraint(op.f('rewards_experiment_id_fkey'), 'rewards', type_='foreignkey')
    op.create_foreign_key('rewards_experiment_id_fkey', 'rewards', 'experiments', ['experiment_id'], ['id'], ondelete='RESTRICT')
    op.add_column('signals', sa.Column('provider_run_id', sa.String(length=128), nullable=True))
    op.add_column('signals', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_signals_provider_run_id'), 'signals', ['provider_run_id'], unique=False)
    op.create_index('uq_signals_org_source_idem', 'signals', ['org_id', 'source', 'idempotency_key'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_signals_org_source_idem', table_name='signals')
    op.drop_index(op.f('ix_signals_provider_run_id'), table_name='signals')
    op.drop_column('signals', 'idempotency_key')
    op.drop_column('signals', 'provider_run_id')
    op.drop_constraint('rewards_experiment_id_fkey', 'rewards', type_='foreignkey')
    op.create_foreign_key(op.f('rewards_experiment_id_fkey'), 'rewards', 'experiments', ['experiment_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('fk_policy_versions_source_experiment', 'policy_versions', type_='foreignkey')
    op.drop_constraint('policy_decisions_incident_id_fkey', 'policy_decisions', type_='foreignkey')
    op.drop_constraint('observations_experiment_id_fkey', 'observations', type_='foreignkey')
    op.create_foreign_key(op.f('observations_experiment_id_fkey'), 'observations', 'experiments', ['experiment_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('uq_observations_source_snapshot', 'observations', type_='unique')
    op.drop_column('observations', 'source_run_id')
    op.drop_index('uq_incidents_open_fingerprint', table_name='incidents', postgresql_where=sa.text("state NOT IN ('closed', 'dismissed', 'failed')"), sqlite_where=sa.text("state NOT IN ('closed', 'dismissed', 'failed')"))
    op.drop_index('ix_incidents_org_state', table_name='incidents')
    op.drop_column('incidents', 'fingerprint')
    op.drop_constraint('experiments_policy_decision_id_fkey', 'experiments', type_='foreignkey')
    op.drop_constraint('experiments_intervention_id_fkey', 'experiments', type_='foreignkey')
    op.drop_constraint('experiments_incident_id_fkey', 'experiments', type_='foreignkey')
    op.create_foreign_key(op.f('experiments_incident_id_fkey'), 'experiments', 'incidents', ['incident_id'], ['id'], ondelete='CASCADE')
    op.create_foreign_key(op.f('experiments_intervention_id_fkey'), 'experiments', 'interventions', ['intervention_id'], ['id'], ondelete='CASCADE')
    op.drop_index(op.f('ix_experiments_target_key'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_policy_decision_id'), table_name='experiments')
    op.drop_column('experiments', 'target_key')
    op.drop_column('experiments', 'override_by')
    op.drop_column('experiments', 'override_reason')
    op.drop_column('experiments', 'policy_action')
    op.drop_column('experiments', 'policy_decision_id')
    op.drop_column('experiments', 'spec')
    op.drop_constraint('executions_intervention_id_fkey', 'executions', type_='foreignkey')
    op.create_foreign_key(op.f('executions_intervention_id_fkey'), 'executions', 'interventions', ['intervention_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('approvals_intervention_id_fkey', 'approvals', type_='foreignkey')
    op.create_foreign_key(op.f('approvals_intervention_id_fkey'), 'approvals', 'interventions', ['intervention_id'], ['id'], ondelete='CASCADE')
    op.drop_index('uq_approvals_one_pending', table_name='approvals', postgresql_where=sa.text("status = 'pending'"), sqlite_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_experiment_outcomes_experiment_id'), table_name='experiment_outcomes')
    op.drop_table('experiment_outcomes')
