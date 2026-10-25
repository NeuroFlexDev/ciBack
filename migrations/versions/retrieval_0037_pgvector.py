"""Add pgvector storage for production dense retrieval.

Revision ID: retrieval_0037
Revises: competency_0036
"""
from alembic import op
import sqlalchemy as sa

revision = "retrieval_0037"
down_revision = "competency_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE chunk_embeddings ADD COLUMN embedding vector(1536)")
    # Convert only vectors that match the rollout model's 1536 dimensions.
    rows = op.get_bind().execute(sa.text(
        "SELECT id, vector FROM chunk_embeddings WHERE model IN ('text-embedding-3-small', 'openai/text-embedding-3-small') "
        "AND jsonb_typeof(vector::jsonb) = 'array'"
    ))
    for row in rows:
        value = row.vector
        if isinstance(value, list) and len(value) == 1536:
            try:
                numbers = [float(item) for item in value]
            except (TypeError, ValueError):
                continue
            if all(__import__("math").isfinite(item) for item in numbers):
                op.get_bind().execute(sa.text(
                    "UPDATE chunk_embeddings SET embedding = CAST(:value AS vector) WHERE id = :id"
                ), {"value": "[" + ",".join(map(str, numbers)) + "]", "id": row.id})
    op.execute("UPDATE chunk_embeddings SET model = 'text-embedding-3-small' "
               "WHERE model = 'openai/text-embedding-3-small' AND embedding IS NOT NULL")
    op.execute("""CREATE INDEX ix_chunk_embeddings_embedding_hnsw
        ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL""")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_embedding_hnsw")
        op.drop_column("chunk_embeddings", "embedding")
