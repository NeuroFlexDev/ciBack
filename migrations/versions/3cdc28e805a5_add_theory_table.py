"""add theory table

Revision ID: 3cdc28e805a5
Revises: 4aa8750105b9
Create Date: 2025-03-28 17:24:27.259730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3cdc28e805a5'
down_revision: Union[str, None] = '4aa8750105b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
