"""Create the portable embedding column on non-PostgreSQL databases.

The ORM maps ``chunk_embeddings.embedding`` to pgvector on PostgreSQL and JSON
elsewhere. The original pgvector migration intentionally skipped SQLite, which
left SQLite schemas behind metadata and caused ``alembic check`` to fail.
"""
from alembic import op
import sqlalchemy as sa

revision = "mobile_0039"
down_revision = "mobile_0038"
branch_labels = None
depends_on = None


def _has_embedding_column() -> bool:
    return any(column["name"] == "embedding" for column in sa.inspect(op.get_bind()).get_columns("chunk_embeddings"))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql" and not _has_embedding_column():
        op.add_column("chunk_embeddings", sa.Column("embedding", sa.JSON(), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql" and _has_embedding_column():
        op.drop_column("chunk_embeddings", "embedding")
