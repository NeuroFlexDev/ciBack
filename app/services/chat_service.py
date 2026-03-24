from __future__ import annotations

import logging
from typing import Any

from app.chat_engine import get_chat_engine
from app.repositories.chat import ChatRepository
from app.schemas.chat import MessageOut
from app.services.embedding_service import search as semantic_search
from app.services.llm_registry import list_models

logger = logging.getLogger(__name__)


def list_available_models() -> list[str]:
    models = list_models()
    logger.debug("Available models count=%s", len(models))
    return models


def convert_messages(raw: list[Any]) -> list[MessageOut]:
    out: list[MessageOut] = []
    for item in raw:
        if isinstance(item, dict):
            role = str(item.get("role", "user"))
            out.append(
                MessageOut(
                    id=int(item.get("id", 0)),
                    author="bot" if role == "assistant" else "user",
                    role=role,
                    text=str(item.get("content", "")),
                    created_at=item.get("created_at"),
                    is_deleted=bool(item.get("is_deleted", False)),
                )
            )
            continue

        role = getattr(item, "role", "user")
        out.append(
            MessageOut(
                id=int(getattr(item, "id")),
                author="bot" if role == "assistant" else "user",
                role=role,
                text=str(getattr(item, "content", "")),
                created_at=getattr(item, "created_at", None),
                is_deleted=bool(getattr(item, "is_deleted", False)),
            )
        )
    return out


def _build_course_context(query_text: str, course_id: int) -> str:
    hits = semantic_search(query_text, k=3, course_id=course_id)
    if not hits:
        return ""

    lines: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        source = hit.get("source_name") or hit.get("type") or "course"
        excerpt = str(hit.get("text", "")).strip()
        if excerpt:
            lines.append(f"{idx}. [{source}] {excerpt}")

    if not lines:
        return ""

    return (
        "Use the following course context when it is relevant to the user's question. "
        "Do not invent facts outside the provided course materials.\n\n"
        + "\n".join(lines)
    )


def chat_generate(
    *,
    chat_id: int,
    user_id: int,
    text: str,
    engine_name: str = "lc_giga",
    model: str | None = None,
    expect_json: bool = False,
    db: Any = None,
    course_id: int | None = None,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    if db is None:
        raise ValueError("db session is required")

    chat = ChatRepository.get_chat(db, chat_id, user_id)
    if chat is None:
        raise KeyError("chat_not_found")

    effective_course_id = course_id if course_id is not None else chat.course_id
    history_records = ChatRepository.get_history(db, chat_id, user_id)
    history_for_llm = [{"role": item.role, "content": item.content} for item in history_records]

    if effective_course_id is not None:
        context_prompt = _build_course_context(text, effective_course_id)
        if context_prompt:
            history_for_llm = [{"role": "system", "content": context_prompt}] + history_for_llm

    user_message = ChatRepository.add_message(db, chat_id, "user", text)
    history_for_llm.append({"role": "user", "content": text})

    engine = get_chat_engine(engine_name, model)
    res = engine.generate(
        history_for_llm,
        model=model,
        expect_json=expect_json,
        max_tokens=max_tokens,
    )

    answer = res.get("text") or res.get("choice") or ""
    bot_message = ChatRepository.add_message(db, chat_id, "assistant", answer)
    return {
        "answer": answer,
        "raw": res,
        "user_msg_id": user_message.id,
        "bot_msg_id": bot_message.id,
        "course_id": effective_course_id,
    }
