"""Private, durable inbox independent of push provider availability."""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from app.database.db import Base


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "event_key", name="uq_notification_user_event"),
        Index("ix_notification_inbox", "user_id", "organization_id", "id"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    event_key = Column(String(200), nullable=False)
    category = Column(String(24), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    resource_type = Column(String(24), nullable=False)
    resource_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    read_at = Column(DateTime, nullable=True)
