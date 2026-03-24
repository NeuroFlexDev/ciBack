from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class Task(Base, BaseModelMixin):
    __tablename__ = "tasks"

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True, index=True)

    module = relationship("Module", back_populates="tasks")
    lesson = relationship("Lesson", back_populates="tasks")
