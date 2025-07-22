from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database.db import Base


class CourseVersion(Base):
    __tablename__ = "course_versions"

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    level = Column(Integer)
    language = Column(Integer)
    created_at = Column(DateTime, default=func.now())

    modules = relationship("ModuleVersion", back_populates="course_version")
