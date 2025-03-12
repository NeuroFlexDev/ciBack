from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database.db import Base

class CourseModule(Base):
    __tablename__ = "course_modules"

    id = Column(Integer, primary_key=True, index=True)
    course_id = Column(Integer, ForeignKey("courses.id"))
    title = Column(String, index=True)
    lessons = Column(Text, default="[]")
    tests = Column(Text, default="[]")
    tasks = Column(Text, default="[]")

    course = relationship("Course", back_populates="modules")
