from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin
from app.models.domain_enums import CourseGraphStatus


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class CourseGraph(Base, BaseModelMixin):
    __tablename__ = "course_graphs"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_course_graphs_version_positive"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_course_graphs_status",
        ),
        UniqueConstraint("course_id", "version", name="uq_course_graphs_course_version"),
        Index("ix_course_graphs_course_status", "course_id", "status"),
    )

    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    nodes = Column(JSON_PAYLOAD, nullable=False, default=list)
    edges = Column(JSON_PAYLOAD, nullable=False, default=list)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default=CourseGraphStatus.DRAFT.value
    )

    course = relationship(
        "Course", back_populates="graphs", foreign_keys=[course_id]
    )
    creator = relationship("User", back_populates="created_graphs")
