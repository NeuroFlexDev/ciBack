"""add owner_id and current_version_id to courses

Revision ID: 1b4264d70bcb
Revises: fb808c2c30c8
Create Date: 2025-07-22 16:34:27.252541

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "1b4264d70bcb"
down_revision: str | None = "fb808c2c30c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
