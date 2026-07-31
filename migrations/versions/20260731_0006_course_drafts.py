"""Add course draft lifecycle fields.

Revision ID: 20260731_0006
Revises: 20260729_0005
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_0006"
down_revision: str | None = "20260729_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("courses") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("is_deleted", sa.Boolean(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE courses SET status = 'ready', "
            "created_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, "
            "is_deleted = false"
        )
    )

    with op.batch_alter_table("courses") as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(), nullable=True)
        batch_op.alter_column(
            "status", existing_type=sa.String(length=32), nullable=False
        )
        batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column("is_deleted", existing_type=sa.Boolean(), nullable=False)
        batch_op.create_check_constraint(
            "ck_courses_status",
            "status IN ('draft', 'configured', 'generating', 'ready', 'generation_failed')",
        )
        batch_op.create_check_constraint(
            "ck_courses_non_draft_name", "status = 'draft' OR name IS NOT NULL"
        )
        batch_op.create_index("ix_courses_status", ["status"], unique=False)
        batch_op.create_index(
            "ix_courses_owner_status", ["owner_id", "status"], unique=False
        )


def downgrade() -> None:
    # The legacy schema cannot represent a NULL title. Preserve draft rows with
    # an empty title instead of deleting them during downgrade.
    op.execute(sa.text("UPDATE courses SET name = '' WHERE name IS NULL"))

    with op.batch_alter_table("courses") as batch_op:
        batch_op.drop_index("ix_courses_owner_status")
        batch_op.drop_index("ix_courses_status")
        batch_op.drop_constraint("ck_courses_non_draft_name", type_="check")
        batch_op.drop_constraint("ck_courses_status", type_="check")
        batch_op.alter_column("name", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("is_deleted")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("status")
