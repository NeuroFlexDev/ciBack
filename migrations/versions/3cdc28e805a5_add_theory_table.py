"""add theory table

Revision ID: 3cdc28e805a5
Revises: 4aa8750105b9
Create Date: 2025-03-28 17:24:27.259730

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "3cdc28e805a5"
down_revision: str | None = "4aa8750105b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    return None


def downgrade() -> None:
    """Downgrade schema."""
    return None
