"""Add persisted upload pipeline runs.

Revision ID: 20260723_0004
Revises: 20260723_0003
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260723_0004"
down_revision: str | None = "20260723_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input_docs", _json_type(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("output", _json_type(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "run_type IN ('document_index', 'graph_generation')",
            name="ck_generation_runs_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_generation_runs_status",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_generation_runs_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_generation_runs_cost_nonnegative",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_runs_id", "generation_runs", ["id"], unique=False
    )
    op.create_index(
        "ix_generation_runs_owner_id",
        "generation_runs",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_runs_course_id",
        "generation_runs",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_runs_document_id",
        "generation_runs",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_runs_owner_status",
        "generation_runs",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_generation_runs_fingerprint",
        "generation_runs",
        ["owner_id", "course_id", "run_type", "input_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_generation_runs_fingerprint", table_name="generation_runs")
    op.drop_index("ix_generation_runs_owner_status", table_name="generation_runs")
    op.drop_index("ix_generation_runs_document_id", table_name="generation_runs")
    op.drop_index("ix_generation_runs_course_id", table_name="generation_runs")
    op.drop_index("ix_generation_runs_owner_id", table_name="generation_runs")
    op.drop_index("ix_generation_runs_id", table_name="generation_runs")
    op.drop_table("generation_runs")
