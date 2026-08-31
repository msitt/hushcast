"""add episode processed_at

Revision ID: f2a41f0c9d17
Revises: e80f3c8e2563
Create Date: 2026-08-30 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

import hushcast.models


revision = 'f2a41f0c9d17'
down_revision = 'e80f3c8e2563'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('episodes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processed_at', hushcast.models.UTCDateTime(timezone=True), nullable=True))
    # backfill so existing processed episodes get a full retention window from
    # roughly when they finished, instead of expiring (or surviving) arbitrarily
    op.execute("UPDATE episodes SET processed_at = updated_at WHERE status = 'processed'")


def downgrade() -> None:
    with op.batch_alter_table('episodes', schema=None) as batch_op:
        batch_op.drop_column('processed_at')
