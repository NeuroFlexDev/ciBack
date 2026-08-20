"""Persist module descriptions used by the Step 5 review contract.

Revision ID: 20260820_0014
Revises: 20260819_0013
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0014"
down_revision: str | None = "20260819_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("modules") as batch:
        batch.add_column(
            sa.Column("description", sa.Text(), nullable=False, server_default="")
        )
    with op.batch_alter_table("module_versions") as batch:
        batch.add_column(
            sa.Column("description", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("module_versions") as batch:
        batch.drop_column("description")
    with op.batch_alter_table("modules") as batch:
        batch.drop_column("description")
