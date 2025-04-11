# app/models/feedback.py

from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.database.db import Base

class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    type = Column(String(50), default="general")  # например: theory, task, test
    comment = Column(Text, nullable=True)
    rating = Column(Integer, nullable=True)  # от 1 до 5

    lesson = relationship("Lesson", back_populates="feedback")
