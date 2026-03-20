"""add role to users

Revision ID: bc9f6d7a1e2c
Revises: 26cab4ad4380
Create Date: 2026-03-21 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bc9f6d7a1e2c"
down_revision: str | None = "26cab4ad4380"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "role",
                    sa.String(),
                    nullable=False,
                    server_default="student",
                )
            )
    else:
        op.add_column(
            "users",
            sa.Column("role", sa.String(), nullable=False, server_default="student"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.drop_column("role")
    else:
        op.drop_column("users", "role")
