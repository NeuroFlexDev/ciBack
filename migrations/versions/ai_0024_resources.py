"""Atomic AI resource admission and checkpoint ownership registry."""
from alembic import op
import sqlalchemy as sa

revision = "ai_0024"
down_revision = "integrations_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ai_resource_mutex", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("revision", sa.Integer(), nullable=False))
    op.create_table("ai_resource_usage",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("key", sa.String(160), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id")),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("generation_runs.id")),
        sa.Column("operation", sa.String(32), nullable=False), sa.Column("lane", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("ocr_pages", sa.Integer(), nullable=False), sa.Column("cost_rub", sa.Numeric(18, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("expires_at", sa.DateTime()), sa.Column("finished_at", sa.DateTime()),
        sa.UniqueConstraint("key", name="uq_ai_resource_usage_key"))
    for field in ("owner_id", "organization_id", "course_id", "run_id"):
        op.create_index(f"ix_ai_resource_usage_{field}", "ai_resource_usage", [field])
    op.create_table("ai_thread_references", sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("generation_runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("purged_at", sa.DateTime()))
    op.create_index("ix_ai_thread_references_run_id", "ai_thread_references", ["run_id"])


def downgrade():
    op.drop_table("ai_thread_references")
    op.drop_table("ai_resource_usage")
    op.drop_table("ai_resource_mutex")
