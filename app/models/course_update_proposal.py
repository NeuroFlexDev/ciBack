from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class CourseUpdateProposal(Base, BaseModelMixin):
    __tablename__ = "course_update_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected', 'applied', 'conflict')",
            name="ck_course_update_proposals_status",
        ),
        Index(
            "ix_course_update_proposals_course_status", "course_id", "status"
        ),
        Index(
            "ix_course_update_proposals_document_status", "document_id", "status"
        ),
        Index(
            "ix_course_update_proposals_base_graph", "base_graph_id"
        ),
        Index(
            "ix_course_update_proposals_detected_run", "detected_by_run_id"
        ),
    )

    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    base_graph_id = Column(
        Integer,
        ForeignKey("course_graphs.id", ondelete="SET NULL"),
        nullable=True,
    )
    detected_by_run_id = Column(
        Integer,
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_versions = Column(JSON_PAYLOAD, nullable=False, default=list)
    source_hashes = Column(JSON_PAYLOAD, nullable=False, default=list)
    affected_node_ids = Column(JSON_PAYLOAD, nullable=False, default=list)
    proposed_diff = Column(JSON_PAYLOAD, nullable=False, default=dict)
    summary = Column(Text, nullable=True)
    status = Column(
        String(32), nullable=False, default="proposed", server_default="proposed"
    )

    course = relationship("Course")
    document = relationship("Document")
    base_graph = relationship("CourseGraph")
    detected_by_run = relationship("GenerationRun")
