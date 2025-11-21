from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Lesson(Base, BaseModelMixin):
    __tablename__ = "lessons"

    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)

    # Обратная связь с Module
    module = relationship("Module", back_populates="lessons")
    theory = relationship(
        "Theory", uselist=False, back_populates="lesson", cascade="all, delete-orphan"
    )
    feedback = relationship("Feedback", back_populates="lesson", cascade="all, delete-orphan")
