from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class CourseModule(Base, BaseModelMixin):
    __tablename__ = "course_modules"

    course_id = Column(Integer, ForeignKey("courses.id"))
    title = Column(String, index=True)
    lessons = Column(Text, default="[]")
    tests = Column(Text, default="[]")
    tasks = Column(Text, default="[]")

    course = relationship("Course", back_populates="course_modules")
