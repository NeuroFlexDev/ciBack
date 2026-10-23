"""Company-created login accounts and one-use email binding."""
from alembic import op
import sqlalchemy as sa
revision = 'accounts_0035'
down_revision = 'ai_0034'
branch_labels = depends_on = None


def upgrade():
    op.add_column('users', sa.Column('username', sa.String(64), nullable=True))
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    with op.batch_alter_table('users') as batch:
        batch.alter_column('email', existing_type=sa.String(), nullable=True)
    op.create_table('email_bindings',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('auth_version', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True))
    op.create_index('ix_email_bindings_user_id', 'email_bindings', ['user_id'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT count(*) FROM users WHERE email IS NULL')).scalar():
        raise RuntimeError('Cannot remove login accounts before their email addresses are bound')
    op.drop_table('email_bindings')
    with op.batch_alter_table('users') as batch:
        batch.alter_column('email', existing_type=sa.String(), nullable=False)
    op.drop_index('ix_users_username', table_name='users')
    op.drop_column('users', 'username')
