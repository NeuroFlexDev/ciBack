"""Explicit operator-managed AI quota exemption; existing limits retain their meaning."""
from alembic import op
import sqlalchemy as sa

revision = 'ai_0034'
down_revision = 'business_0033'
branch_labels = depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('ai_unlimited', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('organizations', 'ai_unlimited')
