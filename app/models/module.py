from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database.db import Base

# Импорты зависимых моделей


class Module(Base):
    __tablename__ = "modules"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"))

    # Обратная связь с Course
    course = relationship("Course", back_populates="modules")

    # Связи с зависимыми моделями (указываем в конце!)
    lessons = relationship("Lesson", back_populates="module", cascade="all, delete-orphan")
    tests = relationship("Test", back_populates="module", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="module", cascade="all, delete-orphan")
