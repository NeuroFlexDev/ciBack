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


class LearningObjective(Base, BaseModelMixin):
    __tablename__ = "learning_objectives"
    __table_args__ = (
        CheckConstraint(
            "NOT (module_id IS NOT NULL AND lesson_id IS NOT NULL)",
            name="ck_learning_objectives_single_detail_scope",
        ),
        Index("ix_learning_objectives_course_deleted", "course_id", "is_deleted"),
    )

    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module_id = Column(
        Integer, ForeignKey("modules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lesson_id = Column(
        Integer, ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bloom_level = Column(String(64), nullable=False)
    measurable_verb = Column(String(128), nullable=False)
    text = Column(Text, nullable=False)
    linked_node_ids = Column(JSON_PAYLOAD, nullable=False, default=list)

    course = relationship("Course", back_populates="learning_objectives")
    module = relationship("Module", back_populates="learning_objectives")
    lesson = relationship("Lesson", back_populates="learning_objectives")
