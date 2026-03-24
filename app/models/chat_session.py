from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class ChatSession(Base, BaseModelMixin):
    __tablename__ = "chat_sessions"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=True, index=True)
    name = Column(String, nullable=False, default="New chat")
    model = Column(String, nullable=True)
    engine = Column(String, nullable=True)

    user = relationship("User", back_populates="chat_sessions")
    course = relationship("Course", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="chat_session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )
