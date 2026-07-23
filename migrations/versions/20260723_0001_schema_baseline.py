"""Create the current Lernium schema baseline.

Revision ID: 20260723_0001
Revises:
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_mixin_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_courses_id", "courses", ["id"], unique=False)
    op.create_index("ix_courses_owner_id", "courses", ["owner_id"], unique=False)

    op.create_table(
        "course_structure",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sections", sa.Integer(), nullable=False),
        sa.Column("tests_per_section", sa.Integer(), nullable=False),
        sa.Column("lessons_per_section", sa.Integer(), nullable=False),
        sa.Column("questions_per_test", sa.Integer(), nullable=False),
        sa.Column("final_test", sa.Boolean(), nullable=True),
        sa.Column("content_types", sa.String(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_course_structure_id", "course_structure", ["id"], unique=False
    )
    op.create_index(
        "ix_course_structure_owner_id",
        "course_structure",
        ["owner_id"],
        unique=False,
    )

    op.create_table(
        "course_modules",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("lessons", sa.Text(), nullable=True),
        sa.Column("tests", sa.Text(), nullable=True),
        sa.Column("tasks", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_modules_id", "course_modules", ["id"], unique=False)
    op.create_index(
        "ix_course_modules_title", "course_modules", ["title"], unique=False
    )

    op.create_table(
        "course_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("language", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_versions_id", "course_versions", ["id"], unique=False)

    op.create_table(
        "modules",
        *_base_mixin_columns(),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_modules_id", "modules", ["id"], unique=False)

    op.create_table(
        "module_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("course_version_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["course_version_id"], ["course_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_module_versions_id", "module_versions", ["id"], unique=False)

    op.create_table(
        "lessons",
        *_base_mixin_columns(),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["module_id"], ["modules.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lessons_id", "lessons", ["id"], unique=False)

    op.create_table(
        "lesson_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("module_version_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["module_version_id"], ["module_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lesson_versions_id", "lesson_versions", ["id"], unique=False)

    op.create_table(
        "tasks",
        *_base_mixin_columns(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["module_id"], ["modules.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_id", "tasks", ["id"], unique=False)

    op.create_table(
        "tests",
        *_base_mixin_columns(),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answers", sa.Text(), nullable=True),
        sa.Column("correct_answer", sa.String(), nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["module_id"], ["modules.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tests_id", "tests", ["id"], unique=False)

    op.create_table(
        "theories",
        *_base_mixin_columns(),
        sa.Column("lesson_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id"], ["lessons.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_theories_id", "theories", ["id"], unique=False)

    op.create_table(
        "feedback",
        *_base_mixin_columns(),
        sa.Column("lesson_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_id", "feedback", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feedback_id", table_name="feedback")
    op.drop_table("feedback")
    op.drop_index("ix_theories_id", table_name="theories")
    op.drop_table("theories")
    op.drop_index("ix_tests_id", table_name="tests")
    op.drop_table("tests")
    op.drop_index("ix_tasks_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_lesson_versions_id", table_name="lesson_versions")
    op.drop_table("lesson_versions")
    op.drop_index("ix_lessons_id", table_name="lessons")
    op.drop_table("lessons")
    op.drop_index("ix_module_versions_id", table_name="module_versions")
    op.drop_table("module_versions")
    op.drop_index("ix_modules_id", table_name="modules")
    op.drop_table("modules")
    op.drop_index("ix_course_versions_id", table_name="course_versions")
    op.drop_table("course_versions")
    op.drop_index("ix_course_modules_title", table_name="course_modules")
    op.drop_index("ix_course_modules_id", table_name="course_modules")
    op.drop_table("course_modules")
    op.drop_index("ix_course_structure_owner_id", table_name="course_structure")
    op.drop_index("ix_course_structure_id", table_name="course_structure")
    op.drop_table("course_structure")
    op.drop_index("ix_courses_owner_id", table_name="courses")
    op.drop_index("ix_courses_id", table_name="courses")
    op.drop_table("courses")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
