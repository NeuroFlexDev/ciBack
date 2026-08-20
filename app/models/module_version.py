from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class ModuleVersion(Base, BaseModelMixin):
    __tablename__ = "module_versions"

    course_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=True)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"), nullable=True, index=True)
    revision = Column(Integer, nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False, default="")
    position = Column(Integer, nullable=False, default=0)
    deleted = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    lessons = relationship("LessonVersion", back_populates="module_version")
    course_version = relationship("CourseVersion", back_populates="modules")
