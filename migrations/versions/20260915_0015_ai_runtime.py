"""Persist scoped AI cache, usage, memory and embeddings.

Revision ID: 20260915_0015
Revises: 20260820_0014
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_0015"
down_revision = "20260820_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("canvas_workspaces",
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False))
    op.create_table("ai_response_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False))
    for col in ("owner_id", "course_id", "expires_at"):
        op.create_index(f"ix_ai_response_cache_{col}", "ai_response_cache", [col])
    op.create_table("ai_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128)))
    op.create_index("ix_ai_calls_run_id", "ai_calls", ["run_id"])
    op.create_table("chat_memory",
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("through_message_id", sa.Integer(), nullable=False))
    op.create_index("ix_chat_memory_owner_id", "chat_memory", ["owner_id"])
    op.create_table("chunk_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chunk_id", "model", name="uq_chunk_embeddings_chunk_model"))
    op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"])


def downgrade():
    for table in ("canvas_workspaces", "chunk_embeddings", "chat_memory", "ai_calls", "ai_response_cache"):
        op.drop_table(table)
