"""Explicit owner-approved support reads and complete learner bookmarks."""
from alembic import op
import sqlalchemy as sa

revision = 'support_0026'
down_revision = 'ai_0025'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('learning_assignments', sa.Column('last_item_key', sa.String(64)))
    op.execute("UPDATE learning_assignments SET last_item_key = 'lesson:' || CAST(last_lesson_id AS VARCHAR) WHERE last_lesson_id IS NOT NULL")
    op.create_table('support_access_grants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('operator_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('scope_type', sa.String(16), nullable=False), sa.Column('scope_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False), sa.Column('requested_hours', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('decided_at', sa.DateTime()), sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('decision_comment', sa.Text(), nullable=False), sa.Column('expires_at', sa.DateTime()),
        sa.Column('revoked_at', sa.DateTime()), sa.Column('revoked_by', sa.Integer(), sa.ForeignKey('users.id')))
    op.create_index('ix_support_access_grants_organization_id', 'support_access_grants', ['organization_id'])
    op.create_index('ix_support_access_grants_operator_id', 'support_access_grants', ['operator_id'])


def downgrade():
    op.drop_table('support_access_grants')
    with op.batch_alter_table('learning_assignments') as batch:
        batch.drop_column('last_item_key')
