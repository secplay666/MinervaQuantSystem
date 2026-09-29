"""chart drawings per user and security

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('chart_drawings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('overlays', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'symbol', name='uq_chart_drawings_user_symbol')
    )


def downgrade() -> None:
    op.drop_table('chart_drawings')
