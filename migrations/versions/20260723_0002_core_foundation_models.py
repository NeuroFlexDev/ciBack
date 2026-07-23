"""Add document, chunk, and course graph foundation models.

Revision ID: 20260723_0002
Revises: 20260723_0001
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260723_0002"
down_revision: str | None = "20260723_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_mixin_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
    ]


def _json_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "documents",
        *_base_mixin_columns(),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("processing_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "size_bytes >= 0", name="ck_documents_size_nonnegative"
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'queued', 'processing', 'indexed', 'failed', 'archived')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_documents_version_positive"),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "version", name="uq_documents_id_version"),
    )
    op.create_index(
        "ix_documents_content_hash", "documents", ["content_hash"], unique=False
    )
    op.create_index("ix_documents_course_id", "documents", ["course_id"], unique=False)
    op.create_index("ix_documents_id", "documents", ["id"], unique=False)
    op.create_index(
        "ix_documents_owner_course",
        "documents",
        ["owner_id", "course_id"],
        unique=False,
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"], unique=False)

    op.create_table(
        "course_graphs",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("nodes", _json_type(), nullable=False),
        sa.Column("edges", _json_type(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_course_graphs_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_course_graphs_version_positive"),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "course_id", "version", name="uq_course_graphs_course_version"
        ),
    )
    op.create_index(
        "ix_course_graphs_course_id", "course_graphs", ["course_id"], unique=False
    )
    op.create_index(
        "ix_course_graphs_course_status",
        "course_graphs",
        ["course_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_course_graphs_created_by", "course_graphs", ["created_by"], unique=False
    )
    op.create_index("ix_course_graphs_id", "course_graphs", ["id"], unique=False)

    op.create_table(
        "document_chunks",
        *_base_mixin_columns(),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding_id", sa.String(length=255), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=512), nullable=True),
        sa.Column("metadata", _json_type(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "document_version > 0",
            name="ck_document_chunks_document_version_positive",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0", name="ck_document_chunks_index_nonnegative"
        ),
        sa.CheckConstraint(
            "page IS NULL OR page > 0", name="ck_document_chunks_page_positive"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "document_version"],
            ["documents.id", "documents.version"],
            name="fk_document_chunks_document_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "document_version",
            "chunk_index",
            name="uq_document_chunks_position",
        ),
    )
    op.create_index("ix_document_chunks_id", "document_chunks", ["id"], unique=False)
    op.create_index(
        "ix_document_chunks_document_version",
        "document_chunks",
        ["document_id", "document_version"],
        unique=False,
    )

    with op.batch_alter_table("courses") as batch_op:
        batch_op.add_column(sa.Column("current_graph_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_courses_current_graph_id",
            "course_graphs",
            ["current_graph_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_courses_current_graph_id", ["current_graph_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("courses") as batch_op:
        batch_op.drop_index("ix_courses_current_graph_id")
        batch_op.drop_constraint(
            "fk_courses_current_graph_id", type_="foreignkey"
        )
        batch_op.drop_column("current_graph_id")

    op.drop_index("ix_document_chunks_document_version", table_name="document_chunks")
    op.drop_index("ix_document_chunks_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_course_graphs_id", table_name="course_graphs")
    op.drop_index("ix_course_graphs_created_by", table_name="course_graphs")
    op.drop_index("ix_course_graphs_course_status", table_name="course_graphs")
    op.drop_index("ix_course_graphs_course_id", table_name="course_graphs")
    op.drop_table("course_graphs")
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_index("ix_documents_owner_course", table_name="documents")
    op.drop_index("ix_documents_id", table_name="documents")
    op.drop_index("ix_documents_course_id", table_name="documents")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_table("documents")
