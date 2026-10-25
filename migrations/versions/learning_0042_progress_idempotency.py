"""Persist idempotency keys for learner progress and bookmarks."""
from alembic import op
import sqlalchemy as sa

revision = "learning_0042"
down_revision = "mobile_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_progress_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assignment_id", sa.Integer(), sa.ForeignKey("learning_assignments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("operation_type", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("assignment_id", "operation_key", name="uq_learning_progress_operation_key"),
    )
    op.create_index("ix_learning_progress_operations_assignment_id", "learning_progress_operations", ["assignment_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_progress_operations_assignment_id", table_name="learning_progress_operations")
    op.drop_table("learning_progress_operations")
