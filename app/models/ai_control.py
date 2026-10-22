"""Operator configuration shared by the API services and background workers."""
from sqlalchemy import Column, Integer, JSON
from app.database.db import Base


class AIControl(Base):
    __tablename__ = "ai_control"
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    providers = Column(JSON, nullable=False, default=list)
    candidates = Column(JSON, nullable=False, default=list)
    policy = Column(JSON, nullable=False, default=dict)
