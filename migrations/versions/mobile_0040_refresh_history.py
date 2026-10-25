"""Ensure refresh-token reuse history exists on already-upgraded installs."""
from alembic import op
import sqlalchemy as sa

revision = "mobile_0040"
down_revision = "mobile_0039"
branch_labels = None
depends_on = None


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table("mobile_refresh_tokens")


def upgrade() -> None:
    if not _has_table():
        op.create_table("mobile_refresh_tokens",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("mobile_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("issued_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("consumed_at", sa.DateTime(), nullable=True))
        op.create_index("ix_mobile_refresh_tokens_session_id", "mobile_refresh_tokens", ["session_id"])


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_mobile_refresh_tokens_session_id", table_name="mobile_refresh_tokens")
        op.drop_table("mobile_refresh_tokens")
