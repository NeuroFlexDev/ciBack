# app/models/theory.py
from sqlalchemy import Column, Integer, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.database.db import Base

class Theory(Base):
    __tablename__ = "theories"

    id = Column(Integer, primary_key=True, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"))
    content = Column(Text, nullable=False)

    lesson = relationship("Lesson", back_populates="theory")
