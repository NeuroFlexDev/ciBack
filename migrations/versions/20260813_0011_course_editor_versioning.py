"""Add entity revisions and editor snapshots.

Revision ID: 20260813_0011
Revises: 20260813_0010
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20260813_0011"
down_revision: str | None = "20260813_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _revision(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
    op.execute(f"UPDATE {table} SET revision = 1")
    with op.batch_alter_table(table) as batch:
        batch.alter_column("revision", nullable=False)
        batch.create_check_constraint(f"ck_{table}_revision_positive", "revision > 0")


def upgrade() -> None:
    for table in ("modules", "lessons", "tests"):
        _revision(table)
    with op.batch_alter_table("module_versions") as batch:
        batch.alter_column("course_version_id", nullable=True)
        batch.add_column(sa.Column("module_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_module_versions_module_id", "modules", ["module_id"], ["id"], ondelete="CASCADE")
        batch.create_foreign_key("fk_module_versions_created_by", "users", ["created_by"], ["id"])
        batch.create_index("ix_module_versions_module_id", ["module_id"])
    with op.batch_alter_table("lesson_versions") as batch:
        batch.alter_column("module_version_id", nullable=True)
        batch.add_column(sa.Column("lesson_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("content", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_lesson_versions_lesson_id", "lessons", ["lesson_id"], ["id"], ondelete="CASCADE")
        batch.create_foreign_key("fk_lesson_versions_created_by", "users", ["created_by"], ["id"])
        batch.create_index("ix_lesson_versions_lesson_id", ["lesson_id"])
    op.create_table(
        "test_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("test_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("assessment_scope", sa.String(16), nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answers", sa.Text(), nullable=False),
        sa.Column("correct_answer", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_test_versions_id", "test_versions", ["id"])
    op.create_index("ix_test_versions_test_id", "test_versions", ["test_id"])


def downgrade() -> None:
    op.drop_index("ix_test_versions_test_id", table_name="test_versions")
    op.drop_index("ix_test_versions_id", table_name="test_versions")
    op.drop_table("test_versions")
    with op.batch_alter_table("lesson_versions") as batch:
        batch.drop_index("ix_lesson_versions_lesson_id")
        batch.drop_constraint("fk_lesson_versions_created_by", type_="foreignkey")
        batch.drop_constraint("fk_lesson_versions_lesson_id", type_="foreignkey")
        for column in ("created_by", "deleted", "position", "content", "revision", "lesson_id"):
            batch.drop_column(column)
        batch.alter_column("module_version_id", nullable=False)
    with op.batch_alter_table("module_versions") as batch:
        batch.drop_index("ix_module_versions_module_id")
        batch.drop_constraint("fk_module_versions_created_by", type_="foreignkey")
        batch.drop_constraint("fk_module_versions_module_id", type_="foreignkey")
        for column in ("created_by", "deleted", "position", "revision", "module_id"):
            batch.drop_column(column)
        batch.alter_column("course_version_id", nullable=False)
    for table in ("tests", "lessons", "modules"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_revision_positive", type_="check")
            batch.drop_column("revision")
