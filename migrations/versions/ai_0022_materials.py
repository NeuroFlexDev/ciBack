"""Protected course images, learner assistant ledger and generation cancellation."""
from alembic import op
import sqlalchemy as sa

revision = "ai_0022"
down_revision = "learning_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("scoped_ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id")),
        sa.Column("operation", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("model", sa.String(255)), sa.Column("provider", sa.String(40)),
        sa.Column("input_tokens", sa.Integer(), nullable=False), sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_scoped_ai_usage_owner_id", "scoped_ai_usage", ["owner_id"])
    op.create_table("organization_material_policy",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("disk_bytes_limit", sa.BigInteger(), nullable=False),
        sa.Column("image_monthly_limit", sa.Integer(), nullable=False),
        sa.Column("chat_monthly_limit", sa.Integer(), nullable=False))
    op.add_column("course_generation_settings", sa.Column("approved_plan", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("course_generation_settings", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("generation_runs", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("generation_runs") as batch:
        batch.add_column(sa.Column("request_id", sa.String(64)))
        batch.create_unique_constraint("uq_generation_runs_owner_request", ["owner_id", "request_id"])
    op.create_table("course_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id"), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_id", sa.String(64)), sa.Column("prompt", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("storage_key", sa.String(512)),
        sa.Column("mime_type", sa.String(32)), sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()),
        sa.Column("provider", sa.String(40)), sa.Column("model", sa.String(255)),
        sa.Column("error_code", sa.String(64)), sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("review_comment", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_course_images_request"))
    op.create_index("ix_course_images_course_id", "course_images", ["course_id"])
    op.create_index("ix_course_images_owner_id", "course_images", ["owner_id"])
    op.create_table("learner_assistant_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("course_version_id", sa.Integer(), sa.ForeignKey("course_versions.id"), nullable=False),
        sa.Column("lesson_id", sa.Integer(), nullable=False), sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False), sa.Column("answer", sa.Text()),
        sa.Column("citations", sa.JSON(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("model", sa.String(255)), sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False), sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_learner_assistant_request"))
    op.create_index("ix_learner_assistant_messages_owner_id", "learner_assistant_messages", ["owner_id"])
    op.create_index("ix_learner_assistant_messages_assignment_id", "learner_assistant_messages", ["assignment_id"])


def downgrade():
    op.drop_table("scoped_ai_usage")
    op.drop_table("organization_material_policy")
    op.drop_column("course_generation_settings", "approved_plan")
    op.drop_column("course_generation_settings", "revision")
    op.drop_table("learner_assistant_messages")
    op.drop_table("course_images")
    op.drop_column("generation_runs", "cancel_requested")
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_constraint("uq_generation_runs_owner_request", type_="unique")
        batch.drop_column("request_id")
