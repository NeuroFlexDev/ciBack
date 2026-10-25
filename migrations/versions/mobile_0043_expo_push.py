"""Store encrypted Expo tokens and user notification preferences."""
from alembic import op
import sqlalchemy as sa

revision = "mobile_0043"
down_revision = "learning_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hash-only registrations cannot be used to deliver and must be re-registered.
    op.execute("DELETE FROM mobile_push_devices")
    op.add_column("mobile_push_devices", sa.Column("token_encrypted", sa.Text(), nullable=True))
    op.create_table(
        "mobile_push_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assignment_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deadline_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("result_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("mobile_push_preferences")
    op.drop_column("mobile_push_devices", "token_encrypted")
