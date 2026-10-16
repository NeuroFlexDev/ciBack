"""Account profile, token revocation and single-use password recovery."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0016"
down_revision = "20260915_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('full_name', sa.String(160), nullable=False, server_default=''))
    op.add_column('users', sa.Column('auth_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table('password_resets', sa.Column('token_hash', sa.String(64), primary_key=True), sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False), sa.Column('expires_at', sa.DateTime(), nullable=False), sa.Column('consumed_at', sa.DateTime(), nullable=True))
    op.create_index('ix_password_resets_user_id', 'password_resets', ['user_id'])


def downgrade():
    op.drop_table('password_resets')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'auth_version')
    op.drop_column('users', 'full_name')
