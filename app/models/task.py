from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Task(Base, BaseModelMixin):
    __tablename__ = "tasks"

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))

    # Обратная связь с Module
    module = relationship("Module", back_populates="tasks")
