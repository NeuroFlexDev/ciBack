"""Encrypted AI provider configuration and live routing policy."""
from alembic import op
import sqlalchemy as sa

revision = "ai_0032"
down_revision = "business_0031"
branch_labels = depends_on = None


def upgrade():
    table = op.create_table("ai_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False))
    op.bulk_insert(table, [{"id": 1, "revision": 0, "providers": [], "candidates": [], "policy": {}}])


def downgrade():
    op.drop_table("ai_control")
