"""position manager: the 大涨后股东户数大增 reminder thresholds

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('pm_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('holder_run', sa.Float(), nullable=False, server_default='0.5'))
        batch_op.add_column(sa.Column('holder_rise', sa.Float(), nullable=False, server_default='0.2'))


def downgrade() -> None:
    with op.batch_alter_table('pm_settings', schema=None) as batch_op:
        batch_op.drop_column('holder_rise')
        batch_op.drop_column('holder_run')
