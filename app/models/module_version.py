from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.database.db import Base

class ModuleVersion(Base):
    __tablename__ = "module_versions"

    id = Column(Integer, primary_key=True)
    course_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=False)
    title = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())

    lessons = relationship("LessonVersion", back_populates="module_version")
    course_version = relationship("CourseVersion", back_populates="modules")
