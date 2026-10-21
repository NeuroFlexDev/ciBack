"""Persist the question before calling AI; same client key never calls AI twice."""
import hashlib
import json
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select
from app.models.chat import Chat, ChatMessage
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.services.chat_service import chat_generate, convert_messages


def find_receipt(db, owner_id, key):
    return db.query(ChatMessage).join(Chat).filter(Chat.owner_id == owner_id,
        ChatMessage.role == "user", ChatMessage.message_metadata["request_id"].as_string() == key).first()


def receipt_out(db, owner_id, receipt):
    try:
        ChatRepository._active_chat(db, receipt.chat_id, owner_id)
    except KeyError:
        raise HTTPException(404, "Чат недоступен") from None
    data = receipt.message_metadata or {}
    if data.get("request_status") == "pending" and receipt.created_at < datetime.utcnow() - timedelta(minutes=10):
        data = {**data, "request_status": "failed", "error_code": "chat_result_unknown"}
        receipt.message_metadata = data
        db.commit()
    return {"chat_id": receipt.chat_id, "request_id": data.get("request_id"), "status": data.get("request_status"),
            "error_code": data.get("error_code"), "text": receipt.content}


def send_once(db, user, payload):
    db.execute(select(User).where(User.id == user.id).with_for_update()).scalar_one()
    digest = hashlib.sha256(json.dumps(payload.model_dump(exclude={"request_id"}), sort_keys=True).encode()).hexdigest()
    previous = find_receipt(db, user.id, payload.request_id)
    if previous:
        if previous.message_metadata.get("request_hash") != digest:
            raise HTTPException(409, {"code": "idempotency_conflict", "message": "Ключ сообщения уже использован для другого текста"})
        receipt = receipt_out(db, user.id, previous)
        if receipt["status"] != "completed":
            raise HTTPException(409, {"code": "chat_request_" + receipt["status"], "message": "Сообщение сохранено. Проверьте состояние запроса перед новой попыткой.", **receipt})
        return convert_messages(ChatRepository.get_history(db, previous.chat_id, user.id))
    if payload.chat_id and payload.chat_id > 0:
        try:
            chat = ChatRepository._active_chat(db, payload.chat_id, user.id)
        except KeyError:
            raise HTTPException(404, "Чат недоступен") from None
    else:
        chat = Chat(owner_id=user.id, title="Новый чат", status="active")
        db.add(chat); db.flush()
    question = ChatMessage(chat_id=chat.id, role="user", content=payload.text,
        message_metadata={"request_id": payload.request_id, "request_hash": digest, "request_status": "pending"})
    db.add(question); db.commit(); db.refresh(question)
    try:
        chat_generate(chat_id=chat.id, user_id=user.id, text=payload.text, db=db,
                      engine_name=payload.engine, model=payload.model, existing_user_message=question)
        question.message_metadata = {**question.message_metadata, "request_status": "completed"}
        db.commit()
    except Exception as exc:
        db.rollback()
        question.message_metadata = {**question.message_metadata, "request_status": "failed",
            "error_code": "chat_context_limit" if isinstance(exc, ValueError) else "chat_request_failed"}
        db.commit()
        if isinstance(exc, ValueError):
            raise HTTPException(422, "Сообщение сохранено, но превышает доступный контекст AI. Сократите его и отправьте новую попытку.") from None
        raise
    return convert_messages(ChatRepository.get_history(db, chat.id, user.id))
