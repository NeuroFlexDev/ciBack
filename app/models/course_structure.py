from sqlalchemy import Boolean, Column, Integer, String
from app.models.base import BaseModelMixin
from app.database.db import Base


class CourseStructure(Base, BaseModelMixin):
    __tablename__ = "course_structure"

    sections = Column(Integer, nullable=False)
    tests_per_section = Column(Integer, nullable=False)
    lessons_per_section = Column(Integer, nullable=False)
    questions_per_test = Column(Integer, nullable=False)
    final_test = Column(Boolean, default=True)
    content_types = Column(String, nullable=True)
