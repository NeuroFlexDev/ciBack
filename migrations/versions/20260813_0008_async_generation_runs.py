"""Add durable async generation job state.

Revision ID: 20260813_0008
Revises: 20260731_0007
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0008"
down_revision: str | None = "20260731_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json():
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def upgrade() -> None:
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_constraint("ck_generation_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_generation_runs_status",
            "status IN ('queued', 'running', 'succeeded', 'completed', 'failed')",
        )
        batch.add_column(sa.Column("input_documents_snapshot", _json(), nullable=True))
        batch.add_column(sa.Column("current_stage", sa.String(64), nullable=True))
        batch.add_column(sa.Column("progress_percent", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("retryable", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("queued_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(), nullable=True))
    op.execute(sa.text("UPDATE generation_runs SET input_documents_snapshot = input_docs, current_stage = status, progress_percent = CASE WHEN status = 'succeeded' THEN 100 ELSE 0 END, retryable = false, attempt = 1, queued_at = created_at"))
    with op.batch_alter_table("generation_runs") as batch:
        batch.alter_column("input_documents_snapshot", existing_type=_json(), nullable=False)
        batch.alter_column("current_stage", existing_type=sa.String(64), nullable=False)
        batch.alter_column("progress_percent", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("retryable", existing_type=sa.Boolean(), nullable=False)
        batch.alter_column("attempt", existing_type=sa.Integer(), nullable=False)
        batch.create_check_constraint("ck_generation_runs_progress", "progress_percent BETWEEN 0 AND 100")
        batch.create_check_constraint("ck_generation_runs_attempt_positive", "attempt > 0")
    predicate = sa.text("run_type = 'graph_generation' AND status IN ('queued', 'running') AND is_deleted = false")
    op.create_index(
        "uq_generation_runs_active_graph_course", "generation_runs", ["course_id"],
        unique=True, postgresql_where=predicate, sqlite_where=predicate,
    )


def downgrade() -> None:
    op.drop_index("uq_generation_runs_active_graph_course", table_name="generation_runs")
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_constraint("ck_generation_runs_attempt_positive", type_="check")
        batch.drop_constraint("ck_generation_runs_progress", type_="check")
        for column in ("finished_at", "started_at", "queued_at", "attempt", "retryable", "error_message", "error_code", "progress_percent", "current_stage", "input_documents_snapshot"):
            batch.drop_column(column)
        batch.drop_constraint("ck_generation_runs_status", type_="check")
        batch.create_check_constraint("ck_generation_runs_status", "status IN ('queued', 'running', 'succeeded', 'failed')")
