"""Versioned legal acceptance receipts and operator requests."""
from alembic import op
import sqlalchemy as sa

revision = '20261021_0019'
down_revision = '20261019_0018'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('legal_receipts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('subject_email', sa.String(255), nullable=False),
        sa.Column('document_slug', sa.String(64), nullable=False),
        sa.Column('document_version', sa.String(32), nullable=False),
        sa.Column('document_sha256', sa.String(64), nullable=False),
        sa.Column('document_snapshot', sa.JSON(), nullable=False),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('context', sa.String(96), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_legal_receipts_user_id', 'legal_receipts', ['user_id'])
    op.create_table('legal_requests',
        sa.Column('reference', sa.String(36), primary_key=True),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_table('legal_requests')
    op.drop_index('ix_legal_receipts_user_id', table_name='legal_receipts')
    op.drop_table('legal_receipts')
