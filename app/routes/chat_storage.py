# app/routes/chat_storage.py
from __future__ import annotations

# Простое in‑memory хранилище (как у тебя было ранее)
CHATS: dict[int, dict] = {}
MESSAGES: dict[int, list[dict]] = {}
CHAT_SEQ = 1
MSG_SEQ = 1


def _ensure_chat_owner(chat_id: int, user_id: int) -> dict:
    chat = CHATS.get(chat_id)
    if not chat or chat["user_id"] != user_id:
        raise KeyError("chat_not_found")
    return chat


def create_chat(user_id: int, name: str = "Новый чат") -> dict:
    global CHAT_SEQ
    chat_id = CHAT_SEQ
    CHAT_SEQ += 1
    CHATS[chat_id] = {
        "id": chat_id,
        "name": name,
        "user_id": user_id,
        "model": None,
        "engine": None,
    }
    MESSAGES[chat_id] = []
    return {"id": chat_id, "name": name}


def list_chats(user_id: int) -> list[dict]:
    return [{"id": c["id"], "name": c["name"]} for c in CHATS.values() if c["user_id"] == user_id]


def get_history(chat_id: int, user_id: int) -> list[dict]:
    _ensure_chat_owner(chat_id, user_id)
    return MESSAGES.get(chat_id, [])


def delete_chat(chat_id: int, user_id: int) -> None:
    _ensure_chat_owner(chat_id, user_id)
    CHATS.pop(chat_id, None)
    MESSAGES.pop(chat_id, None)


def store_user_msg(chat_id: int, text: str) -> int:
    global MSG_SEQ
    MESSAGES[chat_id].append({"id": MSG_SEQ, "role": "user", "content": text})
    MSG_SEQ += 1
    return MSG_SEQ - 1


def store_bot_msg(chat_id: int, text: str) -> int:
    global MSG_SEQ
    MESSAGES[chat_id].append({"id": MSG_SEQ, "role": "assistant", "content": text})
    MSG_SEQ += 1
    return MSG_SEQ - 1


def set_chat_model(chat_id: int, user_id: int, model: str, engine: str | None) -> dict:
    chat = _ensure_chat_owner(chat_id, user_id)
    chat["model"] = model
    chat["engine"] = engine
    return {"id": chat_id, "model": model, "engine": engine}
