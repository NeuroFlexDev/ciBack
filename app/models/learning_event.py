from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


JSON_PAYLOAD = JSON().with_variant(JSONB, "postgresql")


class LearningEvent(Base, BaseModelMixin):
    __tablename__ = "learning_events"
    __table_args__ = (
        Index("ix_learning_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_learning_events_course_occurred", "course_id", "occurred_at"),
    )

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor = Column(JSON_PAYLOAD, nullable=False)
    verb = Column(JSON_PAYLOAD, nullable=False)
    object = Column(JSON_PAYLOAD, nullable=False)
    result = Column(JSON_PAYLOAD, nullable=False, default=dict)
    context = Column(JSON_PAYLOAD, nullable=False, default=dict)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)

    user = relationship("User", back_populates="learning_events")
    course = relationship("Course", back_populates="learning_events")
