"""Explicit learner source release and immutable publication file pins."""
from alembic import op
import sqlalchemy as sa

revision = "materials_0028"
down_revision = "security_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("learner_visible", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("published_source_files",
        sa.Column("course_version_id", sa.Integer(), sa.ForeignKey("course_versions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False))


def downgrade():
    op.drop_table("published_source_files")
    op.drop_column("documents", "learner_visible")
