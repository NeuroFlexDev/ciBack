# app/models/task.py
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database.db import Base

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    module_id = Column(Integer, ForeignKey("modules.id"))

    module = relationship("Module", back_populates="tasks")
