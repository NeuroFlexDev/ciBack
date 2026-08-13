from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Lesson(Base, BaseModelMixin):
    __tablename__ = "lessons"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_lessons_position_nonnegative"),
        CheckConstraint("revision > 0", name="ck_lessons_revision_positive"),
        Index("ix_lessons_module_position", "module_id", "position"),
    )

    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)

    # Обратная связь с Module
    module = relationship("Module", back_populates="lessons")
    theory = relationship(
        "Theory", uselist=False, back_populates="lesson", cascade="all, delete-orphan"
    )
    feedback = relationship("Feedback", back_populates="lesson", cascade="all, delete-orphan")
    learning_objectives = relationship(
        "LearningObjective", back_populates="lesson"
    )
