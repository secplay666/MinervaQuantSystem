"""paper execution: intent fill state, account paper_through

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 10:51:21.706470
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('paper_through', sa.Date(), nullable=True))

    with op.batch_alter_table('order_intents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('filled_qty', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('execution', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('execution_note', sa.Text(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('order_intents', schema=None) as batch_op:
        batch_op.drop_column('execution_note')
        batch_op.drop_column('execution')
        batch_op.drop_column('filled_qty')

    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('paper_through')

