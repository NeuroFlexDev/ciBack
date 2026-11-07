"""Per-organization resource policy overrides."""

import sqlalchemy as sa
from alembic import op

revision = "ai_0025"
down_revision = "ai_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organization_material_policy",
        sa.Column("resource_limits", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("organization_material_policy", "resource_limits")
