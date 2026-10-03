"""widen signals.source to 128 (segmented Profound series labels)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('signals', 'source', existing_type=sa.String(length=64), type_=sa.String(length=128),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column('signals', 'source', existing_type=sa.String(length=128), type_=sa.String(length=64),
                    existing_nullable=False)
