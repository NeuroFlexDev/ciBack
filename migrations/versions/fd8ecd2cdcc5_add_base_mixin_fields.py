"""Add base mixin fields

Revision ID: fd8ecd2cdcc5
Revises: 1b4264d70bcb
Create Date: 2025-11-21 00:54:08.764226

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "fd8ecd2cdcc5"
down_revision: Union[str, None] = "1b4264d70bcb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    return None


def downgrade() -> None:
    """Downgrade schema."""
    return None
