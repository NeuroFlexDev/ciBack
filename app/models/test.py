from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModelMixin
from app.database.db import Base


class Test(Base, BaseModelMixin):
    __tablename__ = "tests"

    question = Column(Text, nullable=False)
    answers = Column(Text, nullable=True)  # Храним JSON-список ответов
    correct_answer = Column(String, nullable=False)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))

    # Обратная связь с Module
    module = relationship("Module", back_populates="tests")
