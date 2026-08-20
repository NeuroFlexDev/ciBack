from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class CourseSourceLink(Base, BaseModelMixin):
    __tablename__ = "course_source_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "document_version"],
            ["documents.id", "documents.version"],
            name="fk_course_source_links_document_version",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "document_version > 0",
            name="ck_course_source_links_document_version_positive",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_course_source_links_chunk_index_nonnegative",
        ),
        CheckConstraint(
            "page IS NULL OR page > 0",
            name="ck_course_source_links_page_positive",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_course_source_links_confidence_range",
        ),
        UniqueConstraint(
            "graph_id",
            "node_id",
            "ref_id",
            "relation",
            name="uq_course_source_links_graph_node_ref_relation",
        ),
        Index(
            "ix_course_source_links_document_version",
            "document_id",
            "document_version",
        ),
        Index(
            "ix_course_source_links_course_target",
            "course_id",
            "target_type",
            "node_id",
        ),
        Index(
            "ix_course_source_links_graph_node", "graph_id", "node_id"
        ),
        Index("ix_course_source_links_run", "run_id"),
    )

    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    graph_id = Column(
        Integer,
        ForeignKey("course_graphs.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_id = Column(
        Integer,
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    node_id = Column(String(255), nullable=False)
    target_type = Column(String(64), nullable=False)

    document_id = Column(Integer, nullable=False)
    document_version = Column(Integer, nullable=False)
    chunk_id = Column(
        Integer,
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_index = Column(Integer, nullable=False)
    page = Column(Integer, nullable=True)
    section = Column(String(512), nullable=True)
    excerpt = Column(Text, nullable=False)
    excerpt_hash = Column(String(128), nullable=False)
    ref_id = Column(
        String(128), nullable=False, default=lambda: uuid4().hex
    )
    relation = Column(
        String(64), nullable=False, default="supports", server_default="supports"
    )
    confidence = Column(Numeric(5, 4), nullable=True)

    course = relationship("Course")
    graph = relationship("CourseGraph")
    run = relationship("GenerationRun")
    document = relationship(
        "Document", foreign_keys=[document_id, document_version]
    )
    chunk = relationship("DocumentChunk", foreign_keys=[chunk_id])
