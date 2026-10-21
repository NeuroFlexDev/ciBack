"""Organization administration and invitations."""
from alembic import op
import sqlalchemy as sa
revision = '20261021_0020'
down_revision = '20261021_0019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('organizations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False, server_default='business'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('seat_limit', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('course_limit', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('source_limit', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('generation_limit', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('business','education','government','individual')", name='ck_organizations_kind'),
        sa.CheckConstraint('seat_limit > 0 AND course_limit > 0 AND source_limit > 0 AND generation_limit >= 0', name='ck_organizations_limits'))
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('organization_role', sa.String(16), nullable=False, server_default='member'))
        batch.create_foreign_key('fk_users_organization_id', 'organizations', ['organization_id'], ['id'])
        batch.create_index('ix_users_organization_id', ['organization_id'])
    op.create_table('invitations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('invited_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("role IN ('owner','member')", name='ck_invitations_role'))
    op.create_index('ix_invitations_email', 'invitations', ['email'])
    op.create_index('ix_invitations_organization_id', 'invitations', ['organization_id'])
    op.create_table('admin_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('target_type', sa.String(32), nullable=False),
        sa.Column('target_id', sa.String(64), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table('admin_events')
    op.drop_table('invitations')
    with op.batch_alter_table('users') as batch:
        batch.drop_index('ix_users_organization_id')
        batch.drop_constraint('fk_users_organization_id', type_='foreignkey')
        batch.drop_column('organization_role')
        batch.drop_column('organization_id')
        batch.drop_column('is_admin')
    op.drop_table('organizations')
