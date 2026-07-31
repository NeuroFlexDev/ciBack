from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class CourseGenerationSettings(Base, BaseModelMixin):
    __tablename__ = "course_generation_settings"
    __table_args__ = (
        UniqueConstraint("course_id", name="uq_course_generation_settings_course_id"),
        CheckConstraint(
            "difficulty IN ('internship', 'basic', 'intermediate', 'advanced')",
            name="ck_course_generation_settings_difficulty",
        ),
        CheckConstraint(
            "language IN ('ru', 'en')",
            name="ck_course_generation_settings_language",
        ),
        CheckConstraint(
            "lesson_count BETWEEN 1 AND 100",
            name="ck_course_generation_settings_lesson_count",
        ),
    )

    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal = Column(Text, nullable=False)
    target_audience = Column(Text, nullable=True)
    difficulty = Column(String(32), nullable=False)
    language = Column(String(8), nullable=False)
    lesson_count = Column(Integer, nullable=False)
    module_tests_enabled = Column(Boolean, nullable=False)
    final_test_enabled = Column(Boolean, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    course = relationship("Course", back_populates="generation_settings")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
