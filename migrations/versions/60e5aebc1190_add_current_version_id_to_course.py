"""Add current_version_id to course

Revision ID: 60e5aebc1190
Revises: 3cdc28e805a5
Create Date: 2025-04-11 11:51:31.228863

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "60e5aebc1190"
down_revision: str | None = "3cdc28e805a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    return None


def downgrade() -> None:
    """Downgrade schema."""
    return None
