"""test modules

Revision ID: 26cab4ad4380
Revises: fd8ecd2cdcc5
Create Date: 2025-12-22 14:52:09.403086

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "26cab4ad4380"
down_revision: Union[str, None] = "fd8ecd2cdcc5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    return None


def downgrade() -> None:
    """Downgrade schema."""
    return None
