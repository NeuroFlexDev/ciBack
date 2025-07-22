"""add owner_id and current_version_id to courses

Revision ID: fb808c2c30c8
Revises: 60e5aebc1190
Create Date: 2025-07-22 16:32:10.426327

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "fb808c2c30c8"
down_revision: str | None = "60e5aebc1190"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
