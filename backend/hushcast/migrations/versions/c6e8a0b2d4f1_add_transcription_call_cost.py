"""add transcription call cost

Revision ID: c6e8a0b2d4f1
Revises: b3d5e7f9a1c2
Create Date: 2026-11-24 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c6e8a0b2d4f1'
down_revision = 'b3d5e7f9a1c2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nullable: the cost of calls made before this is unknown
    with op.batch_alter_table('transcription_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost_usd', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('transcription_calls', schema=None) as batch_op:
        batch_op.drop_column('cost_usd')
