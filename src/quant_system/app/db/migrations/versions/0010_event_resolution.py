"""events: resolved_at and resolution (a later run fixed what the event reported)

Revision ID: 0010
Revises: 0008
Create Date: 2026-11-01 23:30:00

0009 is the position manager's shareholder reminder on the shareholder-counts branch; the two branches meet in a
merge revision there.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resolved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('resolution', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_column('resolution')
        batch_op.drop_column('resolved_at')
