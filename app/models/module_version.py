from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class ModuleVersion(Base, BaseModelMixin):
    __tablename__ = "module_versions"

    course_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=False)
    title = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())

    lessons = relationship("LessonVersion", back_populates="module_version")
    course_version = relationship("CourseVersion", back_populates="modules")
