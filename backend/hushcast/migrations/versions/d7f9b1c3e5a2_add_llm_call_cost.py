"""add llm call cost

Revision ID: d7f9b1c3e5a2
Revises: c6e8a0b2d4f1
Create Date: 2026-11-24 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7f9b1c3e5a2'
down_revision = 'c6e8a0b2d4f1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nullable: None = unknown, 0 = free
    with op.batch_alter_table('llm_calls', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost_usd', sa.Float(), nullable=True))

    # Earlier calls kept the raw provider payload, so a cost reported in the
    # body (OpenRouter's usage.cost) can be recovered. A cost sent only as a
    # header (LiteLLM) was never stored and stays unknown.
    op.execute(
        """
        UPDATE llm_calls
        SET cost_usd = json_extract(response_json, '$.usage.cost')
        WHERE response_json IS NOT NULL
          AND json_valid(response_json)
          AND json_type(response_json, '$.usage.cost') IN ('integer', 'real')
          AND json_extract(response_json, '$.usage.cost') >= 0
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('llm_calls', schema=None) as batch_op:
        batch_op.drop_column('cost_usd')
