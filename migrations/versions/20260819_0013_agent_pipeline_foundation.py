"""Add agent pipeline data foundation.

Revision ID: 20260819_0013
Revises: 20260813_0012
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260819_0013"
down_revision: str | None = "20260813_0012"
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
    # Keep the lineage key nullable until every existing row has a stable value.
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("document_key", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column(
                "is_current",
                sa.Boolean(),
                nullable=True,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column("supersedes_document_id", sa.Integer(), nullable=True)
        )

    documents = sa.table(
        "documents",
        sa.column("id", sa.Integer()),
        sa.column("document_key", sa.String(64)),
        sa.column("is_current", sa.Boolean()),
    )
    op.execute(
        documents.update()
        .where(documents.c.document_key.is_(None))
        .values(
            document_key=sa.literal("legacy-")
            + sa.cast(documents.c.id, sa.String()),
            is_current=sa.true(),
        )
    )

    with op.batch_alter_table("documents") as batch:
        batch.alter_column(
            "document_key", existing_type=sa.String(64), nullable=False
        )
        batch.alter_column(
            "is_current",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )
        batch.create_foreign_key(
            "fk_documents_supersedes_document_id",
            "documents",
            ["supersedes_document_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_documents_course_key_version",
            ["course_id", "document_key", "version"],
        )
        batch.create_unique_constraint(
            "uq_documents_supersedes_document_id",
            ["supersedes_document_id"],
        )

    op.create_index(
        "uq_documents_current_lineage",
        "documents",
        ["course_id", "document_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        "agent_artifacts",
        *_base_mixin_columns(),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("artifact", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="completed"
        ),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("input_fingerprint", sa.String(128), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_agent_artifacts_schema_version_positive"
        ),
        sa.CheckConstraint(
            "sequence >= 0", name="ck_agent_artifacts_sequence_nonnegative"
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_agent_artifacts_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_agent_artifacts_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["generation_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "agent",
            "sequence",
            name="uq_agent_artifacts_run_agent_sequence",
        ),
    )
    op.create_index("ix_agent_artifacts_id", "agent_artifacts", ["id"])
    op.create_index(
        "ix_agent_artifacts_run_status",
        "agent_artifacts",
        ["run_id", "status"],
    )
    op.create_index(
        "ix_agent_artifacts_course_artifact",
        "agent_artifacts",
        ["course_id", "artifact"],
    )

    op.create_table(
        "course_source_links",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("graph_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("node_id", sa.String(255), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(512), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_hash", sa.String(128), nullable=False),
        sa.Column("ref_id", sa.String(128), nullable=False),
        sa.Column(
            "relation", sa.String(64), nullable=False, server_default="supports"
        ),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.CheckConstraint(
            "document_version > 0",
            name="ck_course_source_links_document_version_positive",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_course_source_links_chunk_index_nonnegative",
        ),
        sa.CheckConstraint(
            "page IS NULL OR page > 0",
            name="ck_course_source_links_page_positive",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_course_source_links_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["graph_id"], ["course_graphs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["generation_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "document_version"],
            ["documents.id", "documents.version"],
            name="fk_course_source_links_document_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["document_chunks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "graph_id",
            "node_id",
            "ref_id",
            "relation",
            name="uq_course_source_links_graph_node_ref_relation",
        ),
    )
    op.create_index("ix_course_source_links_id", "course_source_links", ["id"])
    op.create_index(
        "ix_course_source_links_document_version",
        "course_source_links",
        ["document_id", "document_version"],
    )
    op.create_index(
        "ix_course_source_links_course_target",
        "course_source_links",
        ["course_id", "target_type", "node_id"],
    )
    op.create_index(
        "ix_course_source_links_graph_node",
        "course_source_links",
        ["graph_id", "node_id"],
    )
    op.create_index(
        "ix_course_source_links_run", "course_source_links", ["run_id"]
    )

    op.create_table(
        "course_update_proposals",
        *_base_mixin_columns(),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("base_graph_id", sa.Integer(), nullable=True),
        sa.Column("detected_by_run_id", sa.Integer(), nullable=True),
        sa.Column("source_versions", _json_type(), nullable=False),
        sa.Column("source_hashes", _json_type(), nullable=False),
        sa.Column("affected_node_ids", _json_type(), nullable=False),
        sa.Column("proposed_diff", _json_type(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="proposed"
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected', 'applied', 'conflict')",
            name="ck_course_update_proposals_status",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["base_graph_id"], ["course_graphs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["detected_by_run_id"],
            ["generation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_course_update_proposals_id", "course_update_proposals", ["id"]
    )
    op.create_index(
        "ix_course_update_proposals_course_status",
        "course_update_proposals",
        ["course_id", "status"],
    )
    op.create_index(
        "ix_course_update_proposals_document_status",
        "course_update_proposals",
        ["document_id", "status"],
    )
    op.create_index(
        "ix_course_update_proposals_base_graph",
        "course_update_proposals",
        ["base_graph_id"],
    )
    op.create_index(
        "ix_course_update_proposals_detected_run",
        "course_update_proposals",
        ["detected_by_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_update_proposals_detected_run",
        table_name="course_update_proposals",
    )
    op.drop_index(
        "ix_course_update_proposals_base_graph",
        table_name="course_update_proposals",
    )
    op.drop_index(
        "ix_course_update_proposals_document_status",
        table_name="course_update_proposals",
    )
    op.drop_index(
        "ix_course_update_proposals_course_status",
        table_name="course_update_proposals",
    )
    op.drop_index(
        "ix_course_update_proposals_id", table_name="course_update_proposals"
    )
    op.drop_table("course_update_proposals")

    op.drop_index("ix_course_source_links_run", table_name="course_source_links")
    op.drop_index(
        "ix_course_source_links_graph_node", table_name="course_source_links"
    )
    op.drop_index(
        "ix_course_source_links_course_target", table_name="course_source_links"
    )
    op.drop_index(
        "ix_course_source_links_document_version",
        table_name="course_source_links",
    )
    op.drop_index("ix_course_source_links_id", table_name="course_source_links")
    op.drop_table("course_source_links")

    op.drop_index(
        "ix_agent_artifacts_course_artifact", table_name="agent_artifacts"
    )
    op.drop_index("ix_agent_artifacts_run_status", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_id", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")

    op.drop_index("uq_documents_current_lineage", table_name="documents")
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint(
            "uq_documents_supersedes_document_id", type_="unique"
        )
        batch.drop_constraint("uq_documents_course_key_version", type_="unique")
        batch.drop_constraint(
            "fk_documents_supersedes_document_id", type_="foreignkey"
        )
        batch.drop_column("supersedes_document_id")
        batch.drop_column("is_current")
        batch.drop_column("document_key")
