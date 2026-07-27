from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database.db import Base
from app.models.base import BaseModelMixin


class Chat(Base, BaseModelMixin):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')", name="ck_chats_status"
        ),
        Index("ix_chats_owner_status", "owner_id", "status"),
    )

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    course_id = Column(
        Integer, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title = Column(String(255), nullable=False)
    model = Column(String(255), nullable=True)
    engine = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="active")

    owner = relationship("User", back_populates="chats")
    course = relationship("Course", back_populates="chats")
    messages = relationship(
        "ChatMessage",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base, BaseModelMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_messages_role",
        ),
        Index("ix_chat_messages_chat_created_id", "chat_id", "created_at", "id"),
    )

    chat_id = Column(
        Integer, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    model = Column(String(255), nullable=True)
    message_metadata = Column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
    )
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)

    chat = relationship("Chat", back_populates="messages")
