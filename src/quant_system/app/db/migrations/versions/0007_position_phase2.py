"""position manager phase 2: buyback share of a zone, sentinels, quality grades, automatic base switch

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('pm_levels', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fraction', sa.Float(), nullable=True))
    with op.batch_alter_table('pm_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('auto_base', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table('pm_sentinels',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('entered_price', sa.Float(), nullable=False),
    sa.Column('basis', sa.String(length=8), nullable=False),
    sa.Column('factor', sa.Float(), nullable=False),
    sa.Column('direction', sa.String(length=4), nullable=False),
    sa.Column('source_ref', sa.String(length=24), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('crossed_on', sa.Date(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['pm_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pm_sentinels_item_id'), 'pm_sentinels', ['item_id'], unique=False)
    op.create_table('pm_quality',
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('grade', sa.String(length=1), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('dims', sa.JSON(), nullable=False),
    sa.Column('computed_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('symbol')
    )


def downgrade() -> None:
    op.drop_table('pm_quality')
    op.drop_index(op.f('ix_pm_sentinels_item_id'), table_name='pm_sentinels')
    op.drop_table('pm_sentinels')
    with op.batch_alter_table('pm_settings', schema=None) as batch_op:
        batch_op.drop_column('auto_base')
    with op.batch_alter_table('pm_levels', schema=None) as batch_op:
        batch_op.drop_column('fraction')
