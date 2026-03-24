from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class Lesson(Base, BaseModelMixin):
    __tablename__ = "lessons"

    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)

    module = relationship("Module", back_populates="lessons")
    theory = relationship(
        "Theory",
        uselist=False,
        back_populates="lesson",
        cascade="all, delete-orphan",
    )
    tasks = relationship("Task", back_populates="lesson")
    tests = relationship("Test", back_populates="lesson")
    feedback = relationship("Feedback", back_populates="lesson", cascade="all, delete-orphan")
