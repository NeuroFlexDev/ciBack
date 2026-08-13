"""Add generation retry lineage.

Revision ID: 20260813_0009
Revises: 20260813_0008
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260813_0009"
down_revision: str | None = "20260813_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generation_runs") as batch:
        batch.add_column(sa.Column("retry_of_run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_generation_runs_retry_of_run_id",
            "generation_runs",
            ["retry_of_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_generation_runs_retry_of_run_id", ["retry_of_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_index("ix_generation_runs_retry_of_run_id")
        batch.drop_constraint("fk_generation_runs_retry_of_run_id", type_="foreignkey")
        batch.drop_column("retry_of_run_id")
