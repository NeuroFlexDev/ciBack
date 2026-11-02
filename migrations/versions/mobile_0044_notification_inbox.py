"""Durable inbox and push delivery preferences."""
from alembic import op
import sqlalchemy as sa

revision = "mobile_0044"
down_revision = "mobile_0043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("user_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime()),
        sa.UniqueConstraint("user_id", "event_key", name="uq_notification_user_event"))
    op.create_index("ix_notification_inbox", "user_notifications", ["user_id", "organization_id", "id"])
    for name, default in [("push_enabled", True), ("review_enabled", True), ("generation_enabled", True), ("quiet_enabled", False)]:
        op.add_column("mobile_push_preferences", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true() if default else sa.false()))
    for name, default in [("quiet_start", "22:00"), ("quiet_end", "09:00")]:
        op.add_column("mobile_push_preferences", sa.Column(name, sa.String(5), nullable=False, server_default=default))


def downgrade():
    for name in ["push_enabled", "review_enabled", "generation_enabled", "quiet_enabled", "quiet_start", "quiet_end"]:
        op.drop_column("mobile_push_preferences", name)
    op.drop_table("user_notifications")
