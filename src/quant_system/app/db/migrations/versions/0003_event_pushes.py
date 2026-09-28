"""external notifications: event_pushes

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28 16:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('event_pushes',
    sa.Column('event_id', sa.String(length=96), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('attempted_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['events.event_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('event_id', 'channel')
    )


def downgrade() -> None:
    op.drop_table('event_pushes')
