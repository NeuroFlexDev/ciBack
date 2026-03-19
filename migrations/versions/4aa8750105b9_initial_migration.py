"""Initial baseline schema

Revision ID: 4aa8750105b9
Revises:
Create Date: 2025-03-12 10:44:48.392190

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4aa8750105b9"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
    ]


def create_primary_key_index(table_name: str) -> None:
    op.create_index(op.f(f"ix_{table_name}_id"), table_name, ["id"], unique=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        *base_columns(),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("users")
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "courses",
        *base_columns(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("language", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("courses")
    op.create_index(op.f("ix_courses_owner_id"), "courses", ["owner_id"], unique=False)

    op.create_table(
        "course_versions",
        *base_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("language", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("course_versions")

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("courses", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("current_version_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_courses_current_version_id_course_versions",
                "course_versions",
                ["current_version_id"],
                ["id"],
            )
    else:
        op.add_column("courses", sa.Column("current_version_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_courses_current_version_id_course_versions",
            "courses",
            "course_versions",
            ["current_version_id"],
            ["id"],
        )

    op.create_table(
        "course_structure",
        *base_columns(),
        sa.Column("sections", sa.Integer(), nullable=False),
        sa.Column("tests_per_section", sa.Integer(), nullable=False),
        sa.Column("lessons_per_section", sa.Integer(), nullable=False),
        sa.Column("questions_per_test", sa.Integer(), nullable=False),
        sa.Column("final_test", sa.Boolean(), nullable=True),
        sa.Column("content_types", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("course_structure")

    op.create_table(
        "modules",
        *base_columns(),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("modules")

    op.create_table(
        "lessons",
        *base_columns(),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("lessons")

    op.create_table(
        "theories",
        *base_columns(),
        sa.Column("lesson_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("theories")

    op.create_table(
        "tasks",
        *base_columns(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("tasks")

    op.create_table(
        "tests",
        *base_columns(),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answers", sa.Text(), nullable=True),
        sa.Column("correct_answer", sa.String(), nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("tests")

    op.create_table(
        "feedback",
        *base_columns(),
        sa.Column("lesson_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("feedback")

    op.create_table(
        "module_versions",
        *base_columns(),
        sa.Column("course_version_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["course_version_id"], ["course_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("module_versions")

    op.create_table(
        "lesson_versions",
        *base_columns(),
        sa.Column("module_version_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    create_primary_key_index("lesson_versions")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("lesson_versions")
    op.drop_table("module_versions")
    op.drop_table("feedback")
    op.drop_table("tests")
    op.drop_table("tasks")
    op.drop_table("theories")
    op.drop_table("lessons")
    op.drop_table("modules")
    op.drop_table("course_structure")
    op.drop_table("course_versions")
    op.drop_table("courses")
    op.drop_table("users")
