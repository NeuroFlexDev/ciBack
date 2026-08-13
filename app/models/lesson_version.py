from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class LessonVersion(Base, BaseModelMixin):
    __tablename__ = "lesson_versions"

    module_version_id = Column(Integer, ForeignKey("module_versions.id"), nullable=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True, index=True)
    revision = Column(Integer, nullable=True)
    title = Column(String)
    description = Column(Text)
    content = Column(Text, nullable=False, default="")
    position = Column(Integer, nullable=False, default=0)
    deleted = Column(Boolean, nullable=False, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now())

    module_version = relationship("ModuleVersion", back_populates="lessons")
