from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin
from app.models.domain_enums import DocumentStatus


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class Document(Base, BaseModelMixin):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_documents_version_positive"),
        CheckConstraint("size_bytes >= 0", name="ck_documents_size_nonnegative"),
        CheckConstraint(
            "status IN ('uploaded', 'queued', 'processing', 'indexed', 'failed', 'archived')",
            name="ck_documents_status",
        ),
        UniqueConstraint("id", "version", name="uq_documents_id_version"),
        UniqueConstraint(
            "course_id",
            "document_key",
            "version",
            name="uq_documents_course_key_version",
        ),
        UniqueConstraint(
            "supersedes_document_id",
            name="uq_documents_supersedes_document_id",
        ),
        Index("ix_documents_content_hash", "content_hash"),
        Index("ix_documents_owner_course", "owner_id", "course_id"),
        Index(
            "uq_documents_current_lineage",
            "course_id",
            "document_key",
            unique=True,
            postgresql_where=text("is_current = true"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    storage_key = Column(String(1024), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_key = Column(
        String(64), nullable=False, default=lambda: uuid4().hex
    )
    version = Column(Integer, nullable=False, default=1)
    is_current = Column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    supersedes_document_id = Column(
        Integer,
        ForeignKey(
            "documents.id",
            name="fk_documents_supersedes_document_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    status = Column(
        String(32), nullable=False, default=DocumentStatus.UPLOADED.value
    )
    content_hash = Column(String(128), nullable=False)
    source_type = Column(String(64), nullable=False)
    original_filename = Column(String(512), nullable=False)
    mime_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    processing_error = Column(Text, nullable=True)

    owner = relationship("User", back_populates="documents")
    course = relationship("Course", back_populates="documents")
    chunks = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )
    generation_runs = relationship("GenerationRun", back_populates="document")


class DocumentChunk(Base, BaseModelMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "document_version"],
            ["documents.id", "documents.version"],
            ondelete="CASCADE",
            name="fk_document_chunks_document_version",
        ),
        CheckConstraint(
            "document_version > 0",
            name="ck_document_chunks_document_version_positive",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index_nonnegative"),
        CheckConstraint(
            "page IS NULL OR page > 0", name="ck_document_chunks_page_positive"
        ),
        UniqueConstraint(
            "document_id",
            "document_version",
            "chunk_index",
            name="uq_document_chunks_position",
        ),
        Index(
            "ix_document_chunks_document_version",
            "document_id",
            "document_version",
        ),
    )

    document_id = Column(Integer, nullable=False)
    document_version = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding_id = Column(String(255), nullable=True)
    page = Column(Integer, nullable=True)
    section = Column(String(512), nullable=True)
    metadata_json = Column("metadata", JSON_PAYLOAD, nullable=False, default=dict)
    chunk_index = Column(Integer, nullable=False)

    document = relationship("Document", back_populates="chunks")
