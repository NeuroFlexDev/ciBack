"""Personal TOTP factors, one-time login challenges and hashed recovery codes.

Revision ID: security_0027
Revises: support_0026
"""
from alembic import op
import sqlalchemy as sa

revision='security_0027'
down_revision='support_0026'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('user_mfa',
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('secret_encrypted',sa.Text(),nullable=False),
        sa.Column('last_totp_step',sa.BigInteger(),nullable=False),
        sa.Column('pending_secret_encrypted',sa.Text(),nullable=False),
        sa.Column('enrollment_token_hash',sa.String(64),nullable=True),
        sa.Column('enrollment_expires_at',sa.DateTime(),nullable=True),
        sa.Column('failed_attempts',sa.Integer(),nullable=False),
        sa.Column('locked_until',sa.DateTime(),nullable=True),
        sa.Column('enabled_at',sa.DateTime(),nullable=True))
    op.create_table('mfa_recovery_codes',
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('code_hash',sa.String(64),primary_key=True),
        sa.Column('consumed_at',sa.DateTime(),nullable=True))
    op.create_table('mfa_challenges',
        sa.Column('token_hash',sa.String(64),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('auth_version',sa.Integer(),nullable=False),
        sa.Column('primary_method',sa.String(16),nullable=False),
        sa.Column('context',sa.JSON(),nullable=False),
        sa.Column('expires_at',sa.DateTime(),nullable=False),
        sa.Column('consumed_at',sa.DateTime(),nullable=True),
        sa.Column('attempts',sa.Integer(),nullable=False))
    op.create_index('ix_mfa_challenges_user_id','mfa_challenges',['user_id'])
    op.create_index('ix_mfa_challenges_expires_at','mfa_challenges',['expires_at'])


def downgrade():
    op.drop_index('ix_mfa_challenges_expires_at',table_name='mfa_challenges')
    op.drop_index('ix_mfa_challenges_user_id',table_name='mfa_challenges')
    op.drop_table('mfa_challenges')
    op.drop_table('mfa_recovery_codes')
    op.drop_table('user_mfa')
