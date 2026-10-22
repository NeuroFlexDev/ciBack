"""Freeze priced image request options at admission; never reconstruct legacy spend."""
from alembic import op
import sqlalchemy as sa

revision = "ai_0030"
down_revision = "ai_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("course_images", sa.Column("generation_config", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("course_images", "generation_config")
