from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class TestVersion(Base, BaseModelMixin):
    __test__ = False
    __tablename__ = "test_versions"

    test_id = Column(Integer, ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    assessment_scope = Column(String(16), nullable=False)
    module_id = Column(Integer, nullable=True)
    course_id = Column(Integer, nullable=True)
    question = Column(Text, nullable=False)
    answers = Column(Text, nullable=False, default="[]")
    correct_answer = Column(String, nullable=False, default="")
    position = Column(Integer, nullable=False, default=0)
    deleted = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    course_version_id = Column(Integer, ForeignKey("course_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    module_version_id = Column(Integer, ForeignKey("module_versions.id", ondelete="CASCADE"), nullable=True, index=True)

    course_version = relationship("CourseVersion", back_populates="tests")
