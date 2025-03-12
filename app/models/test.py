from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database.db import Base

class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text, nullable=False)
    answers = Column(Text, nullable=True)  # Храним JSON-список ответов
    correct_answer = Column(String, nullable=False)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="CASCADE"))

    # Обратная связь с Module
    module = relationship("Module", back_populates="tests")
