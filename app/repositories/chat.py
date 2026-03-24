from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.course import Course


class ChatRepository:
    @staticmethod
    def create_chat(
        db: Session,
        user_id: int,
        name: str,
        *,
        course_id: int | None = None,
    ) -> ChatSession:
        if course_id is not None:
            course = db.query(Course).filter(Course.id == course_id, Course.is_deleted == False).first()
            if not course:
                raise ValueError("Course not found")

        chat = ChatSession(user_id=user_id, name=name, course_id=course_id)
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return chat

    @staticmethod
    def get_chat(db: Session, chat_id: int, user_id: int) -> ChatSession | None:
        return (
            db.query(ChatSession)
            .filter(
                ChatSession.id == chat_id,
                ChatSession.user_id == user_id,
                ChatSession.is_deleted == False,
            )
            .first()
        )

    @staticmethod
    def list_chats(db: Session, user_id: int) -> list[ChatSession]:
        return (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user_id, ChatSession.is_deleted == False)
            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
            .all()
        )

    @staticmethod
    def get_history(db: Session, chat_id: int, user_id: int) -> list[ChatMessage]:
        chat = ChatRepository.get_chat(db, chat_id, user_id)
        if chat is None:
            raise KeyError("chat_not_found")
        return (
            db.query(ChatMessage)
            .filter(ChatMessage.chat_id == chat_id, ChatMessage.is_deleted == False)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        )

    @staticmethod
    def add_message(db: Session, chat_id: int, role: str, content: str) -> ChatMessage:
        message = ChatMessage(chat_id=chat_id, role=role, content=content)
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    @staticmethod
    def delete_chat(db: Session, chat_id: int, user_id: int) -> None:
        chat = ChatRepository.get_chat(db, chat_id, user_id)
        if chat is None:
            raise KeyError("chat_not_found")

        chat.is_deleted = True
        for message in chat.messages:
            message.is_deleted = True
        db.commit()

    @staticmethod
    def set_chat_model(
        db: Session,
        chat_id: int,
        user_id: int,
        model: str,
        engine: str | None = None,
    ) -> ChatSession:
        chat = ChatRepository.get_chat(db, chat_id, user_id)
        if chat is None:
            raise KeyError("chat_not_found")

        chat.model = model
        chat.engine = engine
        db.commit()
        db.refresh(chat)
        return chat
