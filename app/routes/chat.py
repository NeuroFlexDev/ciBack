# app/routes/chat.py
from __future__ import annotations

import logging
from itertools import count

_fallback_id = count(10_000_000)  # глобальный счетчик
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.routes.auth import User, get_current_user
from app.routes.chat_storage import create_chat as storage_create
from app.routes.chat_storage import delete_chat as storage_delete
from app.routes.chat_storage import get_history, list_chats, set_chat_model
from app.services.chat_service import chat_generate
from app.services.llm_registry import list_models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


# ---------- Schemas ----------
class ChatCreate(BaseModel):
    name: str = "Новый чат"


class ChatOut(BaseModel):
    id: int
    name: str
    model: str | None = None
    engine: str | None = None


class MessageOut(BaseModel):
    id: int
    author: str  # 'user' | 'bot'
    text: str


class MessageIn(BaseModel):
    chat_id: int | None = None
    text: str
    engine: str | None = "lc_giga"
    model: str | None = None


class ModelPatch(BaseModel):
    model: str
    engine: str | None = None


def _convert_messages(raw: list[dict[str, Any]]) -> list[MessageOut]:
    out: list[MessageOut] = []
    for m in raw:
        mid = m.get("id")
        if mid is None:
            mid = next(_fallback_id)  # гарантированно уникальный int
        role = m.get("role", "user")
        author = "bot" if role == "assistant" else "user"
        out.append(MessageOut(id=int(mid), author=author, text=m.get("content", "")))
    return out


# ---------- Routes ----------
@router.get("/models", response_model=list[str])
def get_models_route(request: Request):
    logger.debug("GET /chat/models from %s", request.client)
    models = list_models()
    logger.info("Available models count=%d", len(models))
    return models


@router.post("/", response_model=ChatOut)
def create_chat_route(payload: ChatCreate, user: User = Depends(get_current_user)):
    logger.info("Create chat request user_id=%s name=%s", user.id, payload.name)
    chat = storage_create(user.id, payload.name)
    logger.debug("Chat created: %s", chat)
    return chat


@router.get("/", response_model=list[ChatOut])
def get_chats_route(user: User = Depends(get_current_user)):
    chats = list_chats(user.id)
    logger.debug("List chats user_id=%s -> %d chats", user.id, len(chats))
    return chats


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
def get_msgs_route(chat_id: int, user: User = Depends(get_current_user)):
    msgs = get_history(chat_id, user.id)
    logger.debug("List messages chat_id=%s user_id=%s -> %d msgs", chat_id, user.id, len(msgs))
    return _convert_messages(msgs)


@router.delete("/{chat_id}", status_code=204)
def delete_chat_route(chat_id: int, user: User = Depends(get_current_user)):
    logger.info("Delete chat request chat_id=%s user_id=%s", chat_id, user.id)
    try:
        storage_delete(chat_id, user.id)
    except KeyError:
        logger.warning("Delete chat: not found chat_id=%s user_id=%s", chat_id, user.id)
        raise HTTPException(404, "Chat not found")
    return


@router.patch("/{chat_id}/model", response_model=dict[str, Any])
def patch_model(chat_id: int, payload: ModelPatch, user: User = Depends(get_current_user)):
    logger.info(
        "Patch model chat_id=%s user_id=%s -> model=%s engine=%s",
        chat_id,
        user.id,
        payload.model,
        payload.engine,
    )
    try:
        result = set_chat_model(chat_id, user.id, payload.model, payload.engine)
        logger.debug("Patch model result: %s", result)
        return result
    except KeyError:
        logger.warning("Patch model: chat not found chat_id=%s user_id=%s", chat_id, user.id)
        raise HTTPException(404, "Chat not found")


@router.post("/send", response_model=list[MessageOut])
def send_route(
    msg: MessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logger.info(
        "Send message user_id=%s chat_id=%s engine=%s model=%s text_len=%d",
        user.id,
        msg.chat_id,
        msg.engine,
        msg.model,
        len(msg.text),
    )
    try:
        chat_id = msg.chat_id
        if not chat_id or chat_id <= 0:
            created = storage_create(user.id, "Новый чат")
            chat_id = created["id"]
            logger.debug("Auto-created chat_id=%s for user_id=%s", chat_id, user.id)

        engine = msg.engine or "lc_giga"

        chat_generate(
            chat_id=chat_id,
            user_id=user.id,
            text=msg.text,
            engine_name=engine,
            model=msg.model,
            expect_json=False,
            db=db,
        )

        # Возвращаем всю историю
        msgs = get_history(chat_id, user.id)
        logger.debug("Return history after send: %d msgs", len(msgs))
        return _convert_messages(msgs)

    except HTTPException as e:
        logger.warning("HTTPException in send_route: %s", e.detail)
        raise
    except Exception as e:
        logger.exception("Chat generate error (chat_id=%s user_id=%s)", msg.chat_id, user.id)
        raise HTTPException(500, str(e))
