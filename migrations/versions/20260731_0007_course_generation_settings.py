"""Add course generation settings and run snapshots.

Revision ID: 20260731_0007
Revises: 20260731_0006
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260731_0007"
down_revision: str | None = "20260731_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "course_generation_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("lesson_count", sa.Integer(), nullable=False),
        sa.Column("module_tests_enabled", sa.Boolean(), nullable=False),
        sa.Column("final_test_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "difficulty IN ('internship', 'basic', 'intermediate', 'advanced')",
            name="ck_course_generation_settings_difficulty",
        ),
        sa.CheckConstraint(
            "language IN ('ru', 'en')",
            name="ck_course_generation_settings_language",
        ),
        sa.CheckConstraint(
            "lesson_count BETWEEN 1 AND 100",
            name="ck_course_generation_settings_lesson_count",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", name="uq_course_generation_settings_course_id"),
    )
    op.create_index(
        "ix_course_generation_settings_id",
        "course_generation_settings",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_course_generation_settings_course_id",
        "course_generation_settings",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_generation_settings_created_by",
        "course_generation_settings",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_course_generation_settings_updated_by",
        "course_generation_settings",
        ["updated_by"],
        unique=False,
    )

    with op.batch_alter_table("generation_runs") as batch_op:
        batch_op.add_column(
            sa.Column("settings_snapshot", _json_type(), nullable=True)
        )
    op.execute(sa.text("UPDATE generation_runs SET settings_snapshot = '{}'"))
    with op.batch_alter_table("generation_runs") as batch_op:
        batch_op.alter_column(
            "settings_snapshot", existing_type=_json_type(), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_runs") as batch_op:
        batch_op.drop_column("settings_snapshot")

    op.drop_index(
        "ix_course_generation_settings_updated_by",
        table_name="course_generation_settings",
    )
    op.drop_index(
        "ix_course_generation_settings_created_by",
        table_name="course_generation_settings",
    )
    op.drop_index(
        "ix_course_generation_settings_course_id",
        table_name="course_generation_settings",
    )
    op.drop_index(
        "ix_course_generation_settings_id",
        table_name="course_generation_settings",
    )
    op.drop_table("course_generation_settings")
