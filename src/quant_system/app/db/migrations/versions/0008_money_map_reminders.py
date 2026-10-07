"""money map: the position manager's industry crowding reminder thresholds

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('pm_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('crowd_high', sa.Float(), nullable=False, server_default='1.8'))
        batch_op.add_column(sa.Column('crowd_low', sa.Float(), nullable=False, server_default='1.4'))


def downgrade() -> None:
    with op.batch_alter_table('pm_settings', schema=None) as batch_op:
        batch_op.drop_column('crowd_low')
        batch_op.drop_column('crowd_high')
