from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base

# Импорты зависимых моделей


class Module(Base, BaseModelMixin):
    __tablename__ = "modules"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_modules_position_nonnegative"),
        CheckConstraint("revision > 0", name="ck_modules_revision_positive"),
        Index("ix_modules_course_position", "course_id", "position"),
    )

    title = Column(String, nullable=False)
    description = Column(Text, nullable=False, default="")
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"))
    position = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)

    # Обратная связь с Course
    course = relationship("Course", back_populates="modules")

    # Связи с зависимыми моделями (указываем в конце!)
    lessons = relationship("Lesson", back_populates="module", cascade="all, delete-orphan")
    tests = relationship("Test", back_populates="module", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="module", cascade="all, delete-orphan")
    learning_objectives = relationship(
        "LearningObjective", back_populates="module"
    )
