from sqlalchemy import Column, ForeignKey, Index, Integer, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class AssessmentRubric(Base, BaseModelMixin):
    __tablename__ = "assessment_rubrics"
    __table_args__ = (
        Index("ix_assessment_rubrics_course_deleted", "course_id", "is_deleted"),
    )

    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    competency_id = Column(
        Integer,
        ForeignKey("competencies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    criteria = Column(JSON_PAYLOAD, nullable=False, default=list)
    levels = Column(JSON_PAYLOAD, nullable=False, default=list)

    course = relationship("Course", back_populates="assessment_rubrics")
    task = relationship("Task", back_populates="assessment_rubrics")
    competency = relationship("Competency", back_populates="rubrics")
