"""add episode last_failed_step

Revision ID: 2860c5960bc3
Revises: f2a41f0c9d17
Create Date: 2026-09-02 17:02:32.423977
"""
from alembic import op
import sqlalchemy as sa


revision = '2860c5960bc3'
down_revision = 'f2a41f0c9d17'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('episodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_failed_step', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('episodes', schema=None) as batch_op:
        batch_op.drop_column('last_failed_step')
