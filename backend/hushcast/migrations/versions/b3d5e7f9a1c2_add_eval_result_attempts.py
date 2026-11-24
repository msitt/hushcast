"""add eval result attempts

Revision ID: b3d5e7f9a1c2
Revises: e4a7c19d5b30
Create Date: 2026-11-23 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3d5e7f9a1c2'
down_revision = 'e4a7c19d5b30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills existing rows: they all ran a single detection
    with op.batch_alter_table('eval_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('attempts', sa.Integer(), server_default='1', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('eval_results', schema=None) as batch_op:
        batch_op.drop_column('attempts')
