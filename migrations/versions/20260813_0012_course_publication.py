"""Add course publication state and published snapshots.

Revision ID: 20260813_0012
Revises: 20260813_0011
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260813_0012"
down_revision: str | None = "20260813_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("publication_status", sa.String(16), nullable=False, server_default="draft"))
        batch.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("content_revision", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("ck_courses_publication_status", "publication_status IN ('draft', 'published')")
        batch.create_check_constraint("ck_courses_content_revision_positive", "content_revision > 0")
        batch.create_index("ix_courses_publication_status", ["publication_status"])
        batch.create_index("ix_courses_owner_publication_status", ["owner_id", "publication_status"])

    with op.batch_alter_table("course_versions") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("publication_status", sa.String(16), nullable=False, server_default="published"))
        batch.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
        batch.alter_column("level", existing_type=sa.Integer(), type_=sa.String(), existing_nullable=True)
        batch.alter_column("language", existing_type=sa.Integer(), type_=sa.String(), existing_nullable=True)
    op.execute(sa.text("UPDATE course_versions SET revision = id WHERE revision IS NULL"))
    with op.batch_alter_table("course_versions") as batch:
        batch.alter_column("revision", nullable=False)
        batch.create_foreign_key("fk_course_versions_created_by", "users", ["created_by"], ["id"])
        batch.create_check_constraint("ck_course_versions_revision_positive", "revision > 0")
        batch.create_check_constraint("ck_course_versions_publication_status", "publication_status IN ('draft', 'published')")
        batch.create_unique_constraint("uq_course_versions_course_revision", ["course_id", "revision"])
        batch.create_index("ix_course_versions_course_id", ["course_id"])

    with op.batch_alter_table("test_versions") as batch:
        batch.add_column(sa.Column("course_version_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("module_version_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_test_versions_course_version_id", "course_versions", ["course_version_id"], ["id"], ondelete="CASCADE")
        batch.create_foreign_key("fk_test_versions_module_version_id", "module_versions", ["module_version_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_test_versions_course_version_id", ["course_version_id"])
        batch.create_index("ix_test_versions_module_version_id", ["module_version_id"])


def downgrade() -> None:
    with op.batch_alter_table("test_versions") as batch:
        batch.drop_index("ix_test_versions_module_version_id")
        batch.drop_index("ix_test_versions_course_version_id")
        batch.drop_constraint("fk_test_versions_module_version_id", type_="foreignkey")
        batch.drop_constraint("fk_test_versions_course_version_id", type_="foreignkey")
        batch.drop_column("module_version_id")
        batch.drop_column("course_version_id")
    with op.batch_alter_table("course_versions") as batch:
        batch.drop_index("ix_course_versions_course_id")
        batch.drop_constraint("uq_course_versions_course_revision", type_="unique")
        batch.drop_constraint("ck_course_versions_publication_status", type_="check")
        batch.drop_constraint("ck_course_versions_revision_positive", type_="check")
        batch.drop_constraint("fk_course_versions_created_by", type_="foreignkey")
        batch.alter_column("language", existing_type=sa.String(), type_=sa.Integer(), existing_nullable=True)
        batch.alter_column("level", existing_type=sa.String(), type_=sa.Integer(), existing_nullable=True)
        batch.drop_column("created_by")
        batch.drop_column("published_at")
        batch.drop_column("publication_status")
        batch.drop_column("revision")
    with op.batch_alter_table("courses") as batch:
        batch.drop_index("ix_courses_owner_publication_status")
        batch.drop_index("ix_courses_publication_status")
        batch.drop_constraint("ck_courses_content_revision_positive", type_="check")
        batch.drop_constraint("ck_courses_publication_status", type_="check")
        batch.drop_column("content_revision")
        batch.drop_column("published_at")
        batch.drop_column("publication_status")
