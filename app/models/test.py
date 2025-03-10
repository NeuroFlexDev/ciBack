# app/models/test.py
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database.db import Base

class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    test = Column(String, nullable=False)
    description = Column(String, nullable=True)
    module_id = Column(Integer, ForeignKey("modules.id"))

    module = relationship("Module", back_populates="tests")
