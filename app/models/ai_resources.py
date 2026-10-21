"""Durable admission slots and conservative resource reservations.

Organization IDs are captured at admission, so moving a user cannot move spend.
Content is deliberately absent from this audit ledger.
"""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from app.database.db import Base


class AIResourceMutex(Base):
    __tablename__ = "ai_resource_mutex"
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class AIResourceUsage(Base):
    __tablename__ = "ai_resource_usage"
    __table_args__ = (UniqueConstraint("key", name="uq_ai_resource_usage_key"),)
    id = Column(Integer, primary_key=True)
    key = Column(String(160), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    course_id = Column(Integer, ForeignKey("courses.id"), index=True)
    run_id = Column(Integer, ForeignKey("generation_runs.id"), index=True)
    operation = Column(String(32), nullable=False)
    lane = Column(String(24), nullable=False)
    status = Column(String(24), nullable=False)
    tokens = Column(Integer, nullable=False, default=0)
    ocr_pages = Column(Integer, nullable=False, default=0)
    cost_rub = Column(Numeric(18, 6), nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class AIThreadReference(Base):
    __tablename__ = "ai_thread_references"
    thread_id = Column(String(64), primary_key=True)
    run_id = Column(Integer, ForeignKey("generation_runs.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    purged_at = Column(DateTime)
