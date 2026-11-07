"""Keep filenames and duration for immutable course media."""

import sqlalchemy as sa
from alembic import op

revision = "materials_0044"
down_revision = "mobile_0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("course_images", sa.Column("original_filename", sa.String(255), nullable=True))
    op.add_column("course_images", sa.Column("duration_seconds", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("course_images", "duration_seconds")
    op.drop_column("course_images", "original_filename")
