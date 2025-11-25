# app/models/theory.py
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Theory(Base, BaseModelMixin):
    __tablename__ = "theories"

    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"))
    content = Column(Text, nullable=False)

    lesson = relationship("Lesson", back_populates="theory")
