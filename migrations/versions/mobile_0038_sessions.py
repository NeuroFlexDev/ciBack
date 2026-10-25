"""Revocable native sessions and OIDC handoff marker."""
from alembic import op
import sqlalchemy as sa

revision = "mobile_0038"
down_revision = "retrieval_0037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mobile_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("previous_refresh_token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("device_name", sa.String(120), nullable=False, server_default="Mobile device"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mobile_sessions_user_id", "mobile_sessions", ["user_id"])
    op.create_index("ix_mobile_sessions_expires_at", "mobile_sessions", ["expires_at"])
    op.create_table("mobile_refresh_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mobile_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("consumed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_mobile_refresh_tokens_session_id", "mobile_refresh_tokens", ["session_id"])
    op.add_column("oidc_sessions", sa.Column("mobile_handoff", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("oidc_sessions", "mobile_handoff")
    if sa.inspect(op.get_bind()).has_table("mobile_refresh_tokens"):
        op.drop_index("ix_mobile_refresh_tokens_session_id", table_name="mobile_refresh_tokens")
        op.drop_table("mobile_refresh_tokens")
    op.drop_index("ix_mobile_sessions_expires_at", table_name="mobile_sessions")
    op.drop_index("ix_mobile_sessions_user_id", table_name="mobile_sessions")
    op.drop_table("mobile_sessions")
