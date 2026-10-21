import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.chat import Chat
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.schemas.chat import ChatCreate, ChatRename, ChatOut, MessageIn, MessageOut, ModelPatch
from app.services.auth_service import get_current_user
from app.services.chat_service import chat_generate, convert_messages, list_available_models

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)


def _chat_out(chat: Chat) -> ChatOut:
    return ChatOut(
        id=chat.id,
        course_id=chat.course_id,
        name=chat.title,
        model=chat.model,
        engine=chat.engine,
        is_deleted=chat.is_deleted,
    )


@router.get("/models", response_model=list[str])
def get_models_route(request: Request):
    logger.debug("GET /chat/models from %s", request.client)
    return list_available_models()


@router.post("/", response_model=ChatOut)
def create_chat_route(
    payload: ChatCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return _chat_out(
            ChatRepository.create_chat(db, user.id, payload.name, payload.course_id)
        )
    except KeyError:
        raise HTTPException(404, "Course not found")


@router.get("/", response_model=list[ChatOut])
def get_chats_route(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    course_id: int | None = Query(default=None, gt=0),
):
    return [_chat_out(chat) for chat in ChatRepository.list_chats(db, user.id, limit=limit, offset=offset, course_id=course_id)]


@router.get("/{chat_id}/messages", response_model=list[MessageOut], response_model_exclude_none=True)
def get_msgs_route(
    chat_id: int,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    newest: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        messages = ChatRepository.get_history(
            db, chat_id, user.id, limit=limit, offset=offset, newest=newest
        )
    except KeyError:
        raise HTTPException(404, "Chat not found")
    return convert_messages(messages)


@router.delete("/{chat_id}", status_code=204)
def delete_chat_route(
    chat_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        ChatRepository.delete_chat(db, chat_id, user.id)
    except KeyError:
        raise HTTPException(404, "Chat not found")


@router.patch("/{chat_id}", response_model=ChatOut)
def rename_chat_route(chat_id: int, payload: ChatRename, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        chat = ChatRepository._active_chat(db, chat_id, user.id)
        if not payload.name.strip():
            raise HTTPException(422, "Название чата не может быть пустым")
        chat.title = payload.name.strip()
        db.commit()
        return _chat_out(chat)
    except KeyError:
        raise HTTPException(404, "Chat not found")


@router.patch("/{chat_id}/model", response_model=dict)
def patch_model(
    chat_id: int,
    payload: ModelPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return ChatRepository.set_chat_model(
            db, chat_id, user.id, payload.model, payload.engine
        )
    except KeyError:
        raise HTTPException(404, "Chat not found")


@router.post("/send", response_model=list[MessageOut], response_model_exclude_none=True)
def send_route(
    msg: MessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if msg.request_id:
        from app.services.chat_receipts import send_once
        return send_once(db, user, msg)
    chat_id = msg.chat_id
    if not chat_id or chat_id <= 0:
        chat_id = ChatRepository.create_chat(db, user.id, "Новый чат").id
    try:
        chat_generate(
            chat_id=chat_id,
            user_id=user.id,
            text=msg.text,
            engine_name=msg.engine,
            model=msg.model,
            expect_json=False,
            db=db,
        )
        messages = ChatRepository.get_history(db, chat_id, user.id)
    except KeyError:
        raise HTTPException(404, "Chat not found")
    except ValueError:
        raise HTTPException(422, "Сообщение или история превышают размер контекста AI. Сократите сообщение.") from None
    return convert_messages(messages)


@router.get("/requests/{request_id}")
def request_status(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.chat_receipts import find_receipt, receipt_out
    receipt = find_receipt(db, user.id, request_id)
    if receipt is None:
        raise HTTPException(404, "Запрос ещё не получен сервером")
    return receipt_out(db, user.id, receipt)
