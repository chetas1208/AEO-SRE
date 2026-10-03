"""Laya audit columns on control_policy_decisions.

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("control_policy_decisions", sa.Column("laya_model_version", sa.String(length=128), nullable=True))
    op.add_column("control_policy_decisions", sa.Column("laya_selected_action", sa.String(length=16), nullable=True))
    op.add_column("control_policy_decisions", sa.Column("laya_distribution", _JSON, nullable=False, server_default="{}"))
    op.add_column("control_policy_decisions", sa.Column("laya_confidence", sa.Float(), nullable=True))
    op.add_column("control_policy_decisions", sa.Column("laya_escalation_probability", sa.Float(), nullable=True))
    op.alter_column("control_policy_decisions", "laya_distribution", server_default=None)


def downgrade() -> None:
    op.drop_column("control_policy_decisions", "laya_escalation_probability")
    op.drop_column("control_policy_decisions", "laya_confidence")
    op.drop_column("control_policy_decisions", "laya_distribution")
    op.drop_column("control_policy_decisions", "laya_selected_action")
    op.drop_column("control_policy_decisions", "laya_model_version")
