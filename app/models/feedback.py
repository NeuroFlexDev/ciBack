# app/models/feedback.py
from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Feedback(Base, BaseModelMixin):
    __tablename__ = "feedback"

    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    type = Column(String(50), default="general")
    comment = Column(Text, nullable=True)
    rating = Column(Integer, nullable=True)

    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    author = relationship("User", back_populates="feedback")

    lesson = relationship("Lesson", back_populates="feedback")
