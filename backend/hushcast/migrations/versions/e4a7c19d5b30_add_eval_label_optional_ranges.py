"""add eval label optional ranges

Revision ID: e4a7c19d5b30
Revises: cd551a469c92
Create Date: 2026-11-23 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e4a7c19d5b30'
down_revision = 'cd551a469c92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('eval_labels', schema=None) as batch_op:
        batch_op.add_column(sa.Column('optional_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('eval_labels', schema=None) as batch_op:
        batch_op.drop_column('optional_json')
