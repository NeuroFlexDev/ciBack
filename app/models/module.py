# app/models/module.py
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database.db import Base

class Module(Base):
    __tablename__ = "modules"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    course_id = Column(Integer, ForeignKey("courses.id"))

    # Обратная связь с Course
    course = relationship("Course", back_populates="modules")

    # Связь c Lessons, Tests, Tasks
    lessons = relationship("Lesson", back_populates="module", cascade="all, delete-orphan")
    tests = relationship("Test", back_populates="module", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="module", cascade="all, delete-orphan")
