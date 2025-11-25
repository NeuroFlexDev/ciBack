# app/repositories/chat.py
from app.routes.chat_storage import (
    create_chat as storage_create,
    delete_chat as storage_delete,
    get_history, list_chats, set_chat_model
)


class ChatRepository:
    @staticmethod
    def create_chat(user_id: int, name: str):
        return storage_create(user_id, name)

    @staticmethod
    def delete_chat(chat_id: int, user_id: int):
        return storage_delete(chat_id, user_id)

    @staticmethod
    def list_chats(user_id: int):
        return list_chats(user_id)

    @staticmethod
    def get_history(chat_id: int, user_id: int):
        return get_history(chat_id, user_id)

    @staticmethod
    def set_chat_model(chat_id: int, user_id: int, model: str, engine: str | None = None):
        return set_chat_model(chat_id, user_id, model, engine)
