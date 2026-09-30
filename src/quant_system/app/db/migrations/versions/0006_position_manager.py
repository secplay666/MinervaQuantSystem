"""position manager: library items, labels over time, versioned levels, signals, settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('pm_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=True),
    sa.Column('groups', sa.JSON(), nullable=False),
    sa.Column('star', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=12), nullable=False),
    sa.Column('label_source', sa.String(length=8), nullable=True),
    sa.Column('primary_index', sa.String(length=16), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'symbol', name='uq_pm_items_user_symbol')
    )
    op.create_index(op.f('ix_pm_items_user_id'), 'pm_items', ['user_id'], unique=False)
    op.create_table('pm_label_changes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=12), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('stage_reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['pm_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pm_label_changes_item_id'), 'pm_label_changes', ['item_id'], unique=False)
    op.create_table('pm_levels',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('round_no', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('lower', sa.Float(), nullable=True),
    sa.Column('entered_price', sa.Float(), nullable=False),
    sa.Column('entered_lower', sa.Float(), nullable=True),
    sa.Column('basis', sa.String(length=8), nullable=False),
    sa.Column('factor', sa.Float(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('top_mode', sa.String(length=12), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['pm_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pm_levels_item_kind', 'pm_levels', ['item_id', 'kind', 'status'], unique=False)
    op.create_table('pm_signals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('rule', sa.String(length=24), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('dedupe_key', sa.String(length=96), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['pm_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id', 'dedupe_key', name='uq_pm_signals_item_key')
    )
    op.create_index('ix_pm_signals_user_date', 'pm_signals', ['user_id', 'trade_date'], unique=False)
    op.create_table('pm_settings',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('stage_preset', sa.String(length=16), nullable=False),
    sa.Column('stage_params', sa.JSON(), nullable=False),
    sa.Column('rule_params', sa.JSON(), nullable=False),
    sa.Column('label_mode', sa.String(length=8), nullable=False),
    sa.Column('push_daily', sa.Boolean(), nullable=False),
    sa.Column('evaluated_through', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )


def downgrade() -> None:
    op.drop_table('pm_settings')
    op.drop_index('ix_pm_signals_user_date', table_name='pm_signals')
    op.drop_table('pm_signals')
    op.drop_index('ix_pm_levels_item_kind', table_name='pm_levels')
    op.drop_table('pm_levels')
    op.drop_index(op.f('ix_pm_label_changes_item_id'), table_name='pm_label_changes')
    op.drop_table('pm_label_changes')
    op.drop_index(op.f('ix_pm_items_user_id'), table_name='pm_items')
    op.drop_table('pm_items')
