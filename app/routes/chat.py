import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.schemas.chat import ChatCreate, ChatOut, MessageIn, MessageOut, ModelPatch
from app.services.auth import get_current_user_dep
from app.services.chat_service import chat_generate, convert_messages, list_available_models

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)


@router.get("/models", response_model=list[str])
def get_models_route(request: Request):
    logger.debug("GET /chat/models from %s", request.client)
    models = list_available_models()
    logger.info("Available models count=%d", len(models))
    return models


@router.post("/", response_model=ChatOut)
def create_chat_route(
    payload: ChatCreate,
    user: User = Depends(get_current_user_dep),
    db: Session = Depends(get_db),
):
    try:
        chat = ChatRepository.create_chat(
            db,
            user.id,
            payload.name,
            course_id=payload.course_id,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return ChatOut.model_validate(chat)


@router.get("/", response_model=list[ChatOut])
def get_chats_route(
    user: User = Depends(get_current_user_dep),
    db: Session = Depends(get_db),
):
    chats = ChatRepository.list_chats(db, user.id)
    return [ChatOut.model_validate(item) for item in chats]


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
def get_msgs_route(
    chat_id: int,
    user: User = Depends(get_current_user_dep),
    db: Session = Depends(get_db),
):
    try:
        msgs = ChatRepository.get_history(db, chat_id, user.id)
    except KeyError:
        raise HTTPException(404, "Chat not found")
    return convert_messages(msgs)


@router.delete("/{chat_id}", status_code=204)
def delete_chat_route(
    chat_id: int,
    user: User = Depends(get_current_user_dep),
    db: Session = Depends(get_db),
):
    try:
        ChatRepository.delete_chat(db, chat_id, user.id)
    except KeyError:
        raise HTTPException(404, "Chat not found")
    return


@router.patch("/{chat_id}/model", response_model=ChatOut)
def patch_model(
    chat_id: int,
    payload: ModelPatch,
    user: User = Depends(get_current_user_dep),
    db: Session = Depends(get_db),
):
    try:
        result = ChatRepository.set_chat_model(db, chat_id, user.id, payload.model, payload.engine)
    except KeyError:
        raise HTTPException(404, "Chat not found")
    return ChatOut.model_validate(result)


@router.post("/send", response_model=list[MessageOut])
def send_route(
    msg: MessageIn,
    user: User = Depends(get_current_user_dep),
    db: Session = Depends(get_db),
):
    chat_id = msg.chat_id
    if not chat_id or chat_id <= 0:
        try:
            created = ChatRepository.create_chat(
                db,
                user.id,
                "New chat",
                course_id=msg.course_id,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        chat_id = created.id

    engine = msg.engine or "lc_giga"
    try:
        chat_generate(
            chat_id=chat_id,
            user_id=user.id,
            text=msg.text,
            engine_name=engine,
            model=msg.model,
            expect_json=False,
            db=db,
            course_id=msg.course_id,
        )
        msgs = ChatRepository.get_history(db, chat_id, user.id)
    except KeyError:
        raise HTTPException(404, "Chat not found")
    return convert_messages(msgs)
