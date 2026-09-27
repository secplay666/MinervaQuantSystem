"""initial schema: identity, accounts, ledger, decisions, events

Revision ID: 0001
Revises: (none)
Create Date: 2026-09-28 00:53:21.103124
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('strategy_config', sa.String(length=255), nullable=False),
    sa.Column('initial_cash_fen', sa.BigInteger(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('holdings_confirmed_date', sa.Date(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("mode IN ('manual', 'paper')", name='ck_accounts_mode'),
    sa.PrimaryKeyConstraint('account_id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('events',
    sa.Column('event_id', sa.String(length=96), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('level', sa.String(length=8), nullable=False),
    sa.Column('category', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('run_id', sa.String(length=80), nullable=True),
    sa.Column('trade_date', sa.Date(), nullable=True),
    sa.Column('account_id', sa.String(length=64), nullable=True),
    sa.Column('symbol', sa.String(length=16), nullable=True),
    sa.Column('action_hint', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('event_id')
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_events_at'), ['at'], unique=False)

    op.create_table('permissions',
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('group_name', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('roles',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=128), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('totp_secret', sa.String(length=64), nullable=True),
    sa.Column('failed_logins', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(), nullable=True),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('account_snapshots',
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('cash_fen', sa.BigInteger(), nullable=False),
    sa.Column('market_value_fen', sa.BigInteger(), nullable=False),
    sa.Column('nav_fen', sa.BigInteger(), nullable=False),
    sa.Column('positions', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.PrimaryKeyConstraint('account_id', 'trade_date')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('subject_type', sa.String(length=32), nullable=False),
    sa.Column('subject_id', sa.String(length=96), nullable=False),
    sa.Column('before', sa.JSON(), nullable=True),
    sa.Column('after', sa.JSON(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_at'), ['at'], unique=False)

    op.create_table('decision_runs',
    sa.Column('run_id', sa.String(length=80), nullable=False),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('next_session', sa.Date(), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('gates', sa.JSON(), nullable=False),
    sa.Column('ingest_run_id', sa.String(length=64), nullable=True),
    sa.Column('data_version', sa.String(length=64), nullable=True),
    sa.Column('code_version', sa.JSON(), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=True),
    sa.Column('strategy_id', sa.String(length=64), nullable=True),
    sa.Column('strategy_version', sa.String(length=32), nullable=True),
    sa.Column('nav_fen', sa.BigInteger(), nullable=True),
    sa.Column('cash_fen', sa.BigInteger(), nullable=True),
    sa.Column('positions', sa.Integer(), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('report_dir', sa.String(length=255), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.PrimaryKeyConstraint('run_id')
    )
    with op.batch_alter_table('decision_runs', schema=None) as batch_op:
        batch_op.create_index('ix_decision_runs_account_date', ['account_id', 'trade_date'], unique=False)

    op.create_table('event_reads',
    sa.Column('event_id', sa.String(length=96), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.event_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('event_id', 'user_id')
    )
    op.create_table('imports',
    sa.Column('batch_id', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.PrimaryKeyConstraint('batch_id')
    )
    op.create_table('position_events',
    sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('ref_id', sa.String(length=128), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.PrimaryKeyConstraint('seq'),
    sa.UniqueConstraint('event_id')
    )
    with op.batch_alter_table('position_events', schema=None) as batch_op:
        batch_op.create_index('ix_position_events_account_date', ['account_id', 'trade_date', 'seq'], unique=False)

    op.create_table('position_snapshots',
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.BigInteger(), nullable=False),
    sa.Column('mark_fen', sa.BigInteger(), nullable=False),
    sa.Column('value_fen', sa.BigInteger(), nullable=False),
    sa.Column('cost_fen', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.PrimaryKeyConstraint('account_id', 'trade_date', 'symbol')
    )
    op.create_table('refresh_tokens',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('issued_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_refresh_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('role_permissions',
    sa.Column('role_code', sa.String(length=32), nullable=False),
    sa.Column('permission_code', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['permission_code'], ['permissions.code'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_code'], ['roles.code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_code', 'permission_code')
    )
    op.create_table('user_roles',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role_code', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['role_code'], ['roles.code'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_code')
    )
    op.create_table('order_intents',
    sa.Column('intent_id', sa.String(length=128), nullable=False),
    sa.Column('run_id', sa.String(length=80), nullable=False),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('execute_on', sa.Date(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('qty', sa.BigInteger(), nullable=False),
    sa.Column('proposed_qty', sa.BigInteger(), nullable=False),
    sa.Column('ref_price_fen', sa.BigInteger(), nullable=False),
    sa.Column('limit_up_fen', sa.BigInteger(), nullable=True),
    sa.Column('limit_down_fen', sa.BigInteger(), nullable=True),
    sa.Column('est_notional_fen', sa.BigInteger(), nullable=False),
    sa.Column('est_fees_fen', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('risk', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('valid_until', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("side IN ('buy', 'sell')", name='ck_order_intents_side'),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['decision_runs.run_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('intent_id')
    )
    with op.batch_alter_table('order_intents', schema=None) as batch_op:
        batch_op.create_index('ix_order_intents_account_status', ['account_id', 'status'], unique=False)

    op.create_table('target_positions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=80), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('target_weight', sa.Float(), nullable=False),
    sa.Column('target_qty', sa.BigInteger(), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('explanation', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['decision_runs.run_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'symbol', name='uq_target_positions_run_symbol')
    )
    op.create_table('approvals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('intent_id', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('qty_before', sa.BigInteger(), nullable=True),
    sa.Column('qty_after', sa.BigInteger(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['intent_id'], ['order_intents.intent_id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approvals_intent_id'), ['intent_id'], unique=False)

    op.create_table('fills',
    sa.Column('fill_id', sa.String(length=128), nullable=False),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('intent_id', sa.String(length=128), nullable=True),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('qty', sa.BigInteger(), nullable=False),
    sa.Column('price_fen', sa.BigInteger(), nullable=False),
    sa.Column('commission_fen', sa.BigInteger(), nullable=False),
    sa.Column('stamp_duty_fen', sa.BigInteger(), nullable=False),
    sa.Column('transfer_fee_fen', sa.BigInteger(), nullable=False),
    sa.Column('fees_estimated', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('batch_id', sa.String(length=64), nullable=True),
    sa.Column('reversed_by', sa.String(length=128), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("side IN ('buy', 'sell')", name='ck_fills_side'),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], ),
    sa.ForeignKeyConstraint(['batch_id'], ['imports.batch_id'], ),
    sa.ForeignKeyConstraint(['intent_id'], ['order_intents.intent_id'], ),
    sa.PrimaryKeyConstraint('fill_id')
    )
    with op.batch_alter_table('fills', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fills_account_id'), ['account_id'], unique=False)

    op.create_table('risk_checks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=80), nullable=False),
    sa.Column('intent_id', sa.String(length=128), nullable=True),
    sa.Column('rule_id', sa.String(length=16), nullable=False),
    sa.Column('decision', sa.String(length=8), nullable=False),
    sa.Column('actual', sa.String(length=64), nullable=True),
    sa.Column('limit_value', sa.String(length=64), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['intent_id'], ['order_intents.intent_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['decision_runs.run_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('risk_checks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_risk_checks_run_id'), ['run_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('risk_checks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_risk_checks_run_id'))

    op.drop_table('risk_checks')
    with op.batch_alter_table('fills', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fills_account_id'))

    op.drop_table('fills')
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approvals_intent_id'))

    op.drop_table('approvals')
    op.drop_table('target_positions')
    with op.batch_alter_table('order_intents', schema=None) as batch_op:
        batch_op.drop_index('ix_order_intents_account_status')

    op.drop_table('order_intents')
    op.drop_table('user_roles')
    op.drop_table('role_permissions')
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_user_id'))

    op.drop_table('refresh_tokens')
    op.drop_table('position_snapshots')
    with op.batch_alter_table('position_events', schema=None) as batch_op:
        batch_op.drop_index('ix_position_events_account_date')

    op.drop_table('position_events')
    op.drop_table('imports')
    op.drop_table('event_reads')
    with op.batch_alter_table('decision_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_decision_runs_account_date')

    op.drop_table('decision_runs')
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_at'))

    op.drop_table('audit_log')
    op.drop_table('account_snapshots')
    op.drop_table('users')
    op.drop_table('roles')
    op.drop_table('permissions')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_events_at'))

    op.drop_table('events')
    op.drop_table('accounts')
