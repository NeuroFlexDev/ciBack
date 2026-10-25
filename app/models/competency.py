from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class Competency(Base, BaseModelMixin):
    __tablename__ = "competencies"
    __table_args__ = (
        Index("ix_competencies_course_deleted", "course_id", "is_deleted"),
    )

    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    level = Column(String(64), nullable=True)
    role_id = Column(
        Integer,
        ForeignKey("business_positions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role = relationship("BusinessPosition")
    # Legacy label retained while older generated competency records are read.
    job_role = Column(String(255), nullable=True)

    course = relationship("Course", back_populates="competencies")
    rubrics = relationship("AssessmentRubric", back_populates="competency")
