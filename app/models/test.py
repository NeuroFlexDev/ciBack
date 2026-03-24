from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class Test(Base, BaseModelMixin):
    __tablename__ = "tests"

    question = Column(Text, nullable=False)
    answers = Column(Text, nullable=True)
    correct_answer = Column(String, nullable=False)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True, index=True)

    module = relationship("Module", back_populates="tests")
    lesson = relationship("Lesson", back_populates="tests")
