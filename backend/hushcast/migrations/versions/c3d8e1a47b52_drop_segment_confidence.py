"""drop segment confidence

Revision ID: c3d8e1a47b52
Revises: b69b210f8341
Create Date: 2026-10-28 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d8e1a47b52'
down_revision = 'b69b210f8341'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('segments', schema=None) as batch_op:
        batch_op.drop_column('confidence')


def downgrade() -> None:
    # server_default backfills existing rows, SQLite rejects a NOT NULL add without one
    with op.batch_alter_table('segments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('confidence', sa.Float(), nullable=False, server_default='1.0'))
