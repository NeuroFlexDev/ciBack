from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Test(Base, BaseModelMixin):
    __tablename__ = "tests"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_tests_position_nonnegative"),
        CheckConstraint("revision > 0", name="ck_tests_revision_positive"),
        CheckConstraint(
            "assessment_scope IN ('module', 'final')",
            name="ck_tests_assessment_scope",
        ),
        CheckConstraint(
            "(assessment_scope = 'module' AND module_id IS NOT NULL AND course_id IS NULL) "
            "OR (assessment_scope = 'final' AND module_id IS NULL AND course_id IS NOT NULL)",
            name="ck_tests_parent_scope",
        ),
        Index("ix_tests_module_position", "module_id", "position"),
        Index("ix_tests_course_position", "course_id", "position"),
    )

    question = Column(Text, nullable=False)
    answers = Column(Text, nullable=True)  # Храним JSON-список ответов
    correct_answer = Column(String, nullable=False)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"), nullable=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=True)
    assessment_scope = Column(String(16), nullable=False, default="module")
    position = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)

    # Обратная связь с Module
    module = relationship("Module", back_populates="tests")
    course = relationship("Course", back_populates="final_tests")
