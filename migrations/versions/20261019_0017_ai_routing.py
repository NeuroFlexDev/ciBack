"""Persist adaptive routing decisions without prompt or source content."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0017"
down_revision = "20261015_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_calls", sa.Column("routing", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("ai_calls", "routing")
