from sqlalchemy import Boolean, Column, Integer, String

from app.database.db import Base


class CourseStructure(Base):
    __tablename__ = "course_structure"

    id = Column(Integer, primary_key=True, index=True)
    sections = Column(Integer, nullable=False)
    tests_per_section = Column(Integer, nullable=False)
    lessons_per_section = Column(Integer, nullable=False)
    questions_per_test = Column(Integer, nullable=False)
    final_test = Column(Boolean, default=True)
    content_types = Column(String, nullable=True)
