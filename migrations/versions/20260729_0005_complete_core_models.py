"""Complete the P0 core domain models.

Revision ID: 20260729_0005
Revises: 20260723_0004
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260729_0005"
down_revision: str | None = "20260723_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_mixin_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
    ]


def _json_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "competencies",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", sa.String(length=64), nullable=True),
        sa.Column("job_role", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_competencies_id", "competencies", ["id"], unique=False)
    op.create_index(
        "ix_competencies_course_id", "competencies", ["course_id"], unique=False
    )
    op.create_index(
        "ix_competencies_course_deleted",
        "competencies",
        ["course_id", "is_deleted"],
        unique=False,
    )

    op.create_table(
        "learning_objectives",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("lesson_id", sa.Integer(), nullable=True),
        sa.Column("bloom_level", sa.String(length=64), nullable=False),
        sa.Column("measurable_verb", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("linked_node_ids", _json_type(), nullable=False),
        sa.CheckConstraint(
            "NOT (module_id IS NOT NULL AND lesson_id IS NOT NULL)",
            name="ck_learning_objectives_single_detail_scope",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_objectives_id", "learning_objectives", ["id"], unique=False
    )
    op.create_index(
        "ix_learning_objectives_course_id",
        "learning_objectives",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_objectives_module_id",
        "learning_objectives",
        ["module_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_objectives_lesson_id",
        "learning_objectives",
        ["lesson_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_objectives_course_deleted",
        "learning_objectives",
        ["course_id", "is_deleted"],
        unique=False,
    )

    op.create_table(
        "assessment_rubrics",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("competency_id", sa.Integer(), nullable=True),
        sa.Column("criteria", _json_type(), nullable=False),
        sa.Column("levels", _json_type(), nullable=False),
        sa.ForeignKeyConstraint(
            ["competency_id"], ["competencies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_rubrics_id", "assessment_rubrics", ["id"], unique=False
    )
    op.create_index(
        "ix_assessment_rubrics_course_id",
        "assessment_rubrics",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_assessment_rubrics_task_id",
        "assessment_rubrics",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_assessment_rubrics_competency_id",
        "assessment_rubrics",
        ["competency_id"],
        unique=False,
    )
    op.create_index(
        "ix_assessment_rubrics_course_deleted",
        "assessment_rubrics",
        ["course_id", "is_deleted"],
        unique=False,
    )

    op.create_table(
        "approvals",
        *_base_mixin_columns(),
        sa.Column("course_graph_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("diff", _json_type(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'changes_requested')",
            name="ck_approvals_decision",
        ),
        sa.ForeignKeyConstraint(
            ["course_graph_id"], ["course_graphs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_id", "approvals", ["id"], unique=False)
    op.create_index(
        "ix_approvals_course_graph_id",
        "approvals",
        ["course_graph_id"],
        unique=False,
    )
    op.create_index(
        "ix_approvals_reviewer_id", "approvals", ["reviewer_id"], unique=False
    )
    op.create_index(
        "ix_approvals_graph_decision",
        "approvals",
        ["course_graph_id", "decision"],
        unique=False,
    )

    op.create_table(
        "learning_events",
        *_base_mixin_columns(),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("actor", _json_type(), nullable=False),
        sa.Column("verb", _json_type(), nullable=False),
        sa.Column("object", _json_type(), nullable=False),
        sa.Column("result", _json_type(), nullable=False),
        sa.Column("context", _json_type(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_events_id", "learning_events", ["id"], unique=False)
    op.create_index(
        "ix_learning_events_user_id", "learning_events", ["user_id"], unique=False
    )
    op.create_index(
        "ix_learning_events_course_id",
        "learning_events",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_events_occurred_at",
        "learning_events",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_learning_events_user_occurred",
        "learning_events",
        ["user_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_learning_events_course_occurred",
        "learning_events",
        ["course_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_learning_events_course_occurred", table_name="learning_events")
    op.drop_index("ix_learning_events_user_occurred", table_name="learning_events")
    op.drop_index("ix_learning_events_occurred_at", table_name="learning_events")
    op.drop_index("ix_learning_events_course_id", table_name="learning_events")
    op.drop_index("ix_learning_events_user_id", table_name="learning_events")
    op.drop_index("ix_learning_events_id", table_name="learning_events")
    op.drop_table("learning_events")

    op.drop_index("ix_approvals_graph_decision", table_name="approvals")
    op.drop_index("ix_approvals_reviewer_id", table_name="approvals")
    op.drop_index("ix_approvals_course_graph_id", table_name="approvals")
    op.drop_index("ix_approvals_id", table_name="approvals")
    op.drop_table("approvals")

    op.drop_index(
        "ix_assessment_rubrics_course_deleted", table_name="assessment_rubrics"
    )
    op.drop_index(
        "ix_assessment_rubrics_competency_id", table_name="assessment_rubrics"
    )
    op.drop_index("ix_assessment_rubrics_task_id", table_name="assessment_rubrics")
    op.drop_index("ix_assessment_rubrics_course_id", table_name="assessment_rubrics")
    op.drop_index("ix_assessment_rubrics_id", table_name="assessment_rubrics")
    op.drop_table("assessment_rubrics")

    op.drop_index(
        "ix_learning_objectives_course_deleted", table_name="learning_objectives"
    )
    op.drop_index(
        "ix_learning_objectives_lesson_id", table_name="learning_objectives"
    )
    op.drop_index(
        "ix_learning_objectives_module_id", table_name="learning_objectives"
    )
    op.drop_index(
        "ix_learning_objectives_course_id", table_name="learning_objectives"
    )
    op.drop_index("ix_learning_objectives_id", table_name="learning_objectives")
    op.drop_table("learning_objectives")

    op.drop_index("ix_competencies_course_deleted", table_name="competencies")
    op.drop_index("ix_competencies_course_id", table_name="competencies")
    op.drop_index("ix_competencies_id", table_name="competencies")
    op.drop_table("competencies")
