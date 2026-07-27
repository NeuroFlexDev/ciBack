from sqlalchemy.orm import Session

from app.models.chat import Chat, ChatMessage
from app.models.course import Course


class ChatRepository:
    @staticmethod
    def _active_chat(db: Session, chat_id: int, user_id: int) -> Chat:
        chat = db.query(Chat).filter(
            Chat.id == chat_id,
            Chat.owner_id == user_id,
            Chat.status == "active",
            Chat.is_deleted.is_(False),
        ).first()
        if chat is None:
            raise KeyError("chat_not_found")
        return chat

    @staticmethod
    def create_chat(db: Session, user_id: int, name: str, course_id: int | None = None) -> Chat:
        if course_id is not None and db.query(Course.id).filter(
            Course.id == course_id, Course.owner_id == user_id
        ).first() is None:
            raise KeyError("course_not_found")
        chat = Chat(owner_id=user_id, course_id=course_id, title=name, status="active")
        db.add(chat)
        db.commit()
        db.refresh(chat)
        return chat

    @staticmethod
    def list_chats(db: Session, user_id: int) -> list[Chat]:
        return db.query(Chat).filter(
            Chat.owner_id == user_id,
            Chat.status == "active",
            Chat.is_deleted.is_(False),
        ).order_by(Chat.created_at.desc(), Chat.id.desc()).all()

    @staticmethod
    def get_history(
        db: Session, chat_id: int, user_id: int, *, limit: int | None = None, offset: int = 0
    ) -> list[ChatMessage]:
        ChatRepository._active_chat(db, chat_id, user_id)
        query = db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id, ChatMessage.is_deleted.is_(False)
        ).order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    @staticmethod
    def get_recent_history(
        db: Session, chat_id: int, user_id: int, limit: int
    ) -> list[ChatMessage]:
        ChatRepository._active_chat(db, chat_id, user_id)
        messages = db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id, ChatMessage.is_deleted.is_(False)
        ).order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit).all()
        return list(reversed(messages))

    @staticmethod
    def add_message(
        db: Session, chat_id: int, role: str, content: str, *,
        model: str | None = None, metadata: dict | None = None,
        prompt_tokens: int | None = None, completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            chat_id=chat_id, role=role, content=content, model=model,
            message_metadata=metadata or {}, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, total_tokens=total_tokens,
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    @staticmethod
    def delete_chat(db: Session, chat_id: int, user_id: int) -> None:
        chat = ChatRepository._active_chat(db, chat_id, user_id)
        chat.status = "archived"
        chat.is_deleted = True
        db.commit()

    @staticmethod
    def set_chat_model(
        db: Session, chat_id: int, user_id: int, model: str, engine: str | None = None
    ) -> dict:
        chat = ChatRepository._active_chat(db, chat_id, user_id)
        chat.model = model
        chat.engine = engine
        db.commit()
        return {"id": chat.id, "model": chat.model, "engine": chat.engine}
