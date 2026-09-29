"""invitation codes: invitations, users.invitation_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('invitations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('hint', sa.String(length=16), nullable=False),
    sa.Column('roles', sa.JSON(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('max_uses', sa.Integer(), nullable=False),
    sa.Column('used_count', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('invitation_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_users_invitation_id', 'invitations', ['invitation_id'], ['id'],
                                    ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_invitation_id', type_='foreignkey')
        batch_op.drop_column('invitation_id')
    op.drop_table('invitations')
