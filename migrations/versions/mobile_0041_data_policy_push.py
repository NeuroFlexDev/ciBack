"""Add session-bound mobile push device registrations."""
from alembic import op
import sqlalchemy as sa

revision = "mobile_0041"
down_revision = "mobile_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mobile_push_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("mobile_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mobile_push_devices_session_id", "mobile_push_devices", ["session_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_mobile_push_devices_session_id", table_name="mobile_push_devices")
    op.drop_table("mobile_push_devices")
