from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database.db import Base


class LessonVersion(Base):
    __tablename__ = "lesson_versions"

    id = Column(Integer, primary_key=True)
    module_version_id = Column(Integer, ForeignKey("module_versions.id"), nullable=False)
    title = Column(String)
    description = Column(Text)
    created_at = Column(DateTime, default=func.now())

    module_version = relationship("ModuleVersion", back_populates="lessons")
