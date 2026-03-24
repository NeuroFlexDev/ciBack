from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class ChatMessage(Base, BaseModelMixin):
    __tablename__ = "chat_messages"

    chat_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)

    chat_session = relationship("ChatSession", back_populates="messages")
