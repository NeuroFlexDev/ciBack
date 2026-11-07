"""Retain removed company memberships and their learning history."""

import sqlalchemy as sa
from alembic import op

revision = "business_0046"
down_revision = "release_0045"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("organization_removed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("users", "organization_removed_at")
