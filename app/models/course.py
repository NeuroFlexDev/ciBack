# app/models/course.py
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Course(Base, BaseModelMixin):
    __tablename__ = "courses"

    name = Column(String, nullable=False)
    description = Column(String)
    level = Column(Integer)  # лучше привести к int, см. ниже
    language = Column(Integer)

    # ✅ FK на пользователя
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # связи
    owner = relationship("User", back_populates="courses")
    modules = relationship("Module", back_populates="course", cascade="all, delete-orphan")

    current_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=True)
    current_version = relationship("CourseVersion", foreign_keys=[current_version_id])
