from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database.db import Base


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)

    # Обратная связь с Module
    module = relationship("Module", back_populates="lessons")
    theory = relationship(
        "Theory", uselist=False, back_populates="lesson", cascade="all, delete-orphan"
    )
    feedback = relationship("Feedback", back_populates="lesson", cascade="all, delete-orphan")
