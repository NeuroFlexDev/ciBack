from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database.db import Base

class Course(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    level = Column(String, nullable=True)
    language = Column(String, nullable=True)

    # Связь с модулями
    modules = relationship("Module", back_populates="course", cascade="all, delete-orphan")
    current_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=True)
    current_version = relationship("CourseVersion", foreign_keys=[current_version_id])