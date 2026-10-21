from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Task(Base, BaseModelMixin):
    __tablename__ = "tasks"

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    revision = Column(Integer, nullable=False, default=1, server_default='1')
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))

    # Обратная связь с Module
    module = relationship("Module", back_populates="tasks")
    assessment_rubrics = relationship("AssessmentRubric", back_populates="task")
