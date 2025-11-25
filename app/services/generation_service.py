# app/services/generation_service.py
from __future__ import annotations

import json
import logging
import re
import traceback
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from app.services.external_sources import aggregated_search
from app.services.feedback_service import get_feedback_summary
from app.services.gigachat_service import get_available_giga_models, get_gigachat_client
from app.services.hf_infer_service import get_available_hf_models, get_hf_client
from app.services.llm_types import LLMClient

# перенесено из app/routes/generation.py

def course_kwargs(course, cs=None):
    data = {
        "course_name": course.name,
        "course_description": course.description,
        "course_level": course.level,
    }
    if cs:
        data.update({
            "module_count": cs.sections,
            "lessons_per_section": cs.lessons_per_section,
        })
    return data


def llm_json(template: str, *, engine: str, model: str | None, **kwargs) -> dict[str, Any]:
    """
    Унифицированный обёртка: отдаёт результат LLM с парсингом и обработкой ошибок,
    проводит логи по ошибкам и хендлит HTTP-exception.
    """
    try:
        return generate_from_prompt(
            template_name=template,
            engine=engine,
            model_name=model,
            expect_json=True,
            include_external_context=False,
            use_feedback=False,
            **kwargs,
        )
    except Exception as e:
        logger.error("LLM error in llm_json: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"Ошибка генерации LLM: {e}")

# -------------------- Setup --------------------
env = Environment(loader=FileSystemLoader("app/prompts"))
logger = logging.getLogger(__name__)

# Фабрики клиентов
SUPPORTED_ENGINES: dict[str, Callable[[str | None], tuple[LLMClient, str]]] = {
    "hf_api": get_hf_client,
    "gigachat": get_gigachat_client,
}

# Алиасы от старого кода/фронта
ENGINE_ALIASES: dict[str, str] = {
    "lc_giga": "gigachat",
    "lc_gc": "gigachat",
    "lc_hf": "hf_api",
}

DEFAULT_ENGINE = "gigachat"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_MODEL: str | None = None

# -------------------- Utils --------------------
JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _safe_json_loads(raw: str) -> dict[str, Any]:
    """
    Пытаемся вытащить валидный JSON из ответа модели.
    1) Убираем обёртки ```json ... ```
    2) Пробуем загрузить напрямую
    3) Ищем самый длинный {...} блок
    4) Мини-фиксы (одиночные кавычки -> двойные)
    """
    cleaned = raw.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    # Прямая попытка
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Ищем блоки {...}
    candidates = JSON_BLOCK_RE.findall(cleaned)
    candidates.sort(key=len, reverse=True)
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:
            # Простейший фикс кавычек
            fixed = re.sub(r"(?<!\\)'", '"', c)
            try:
                return json.loads(fixed)
            except Exception:
                continue

    raise HTTPException(500, "Ошибка разбора JSON от LLM")


@lru_cache(maxsize=128)
def get_cached_external_context(query: str, lang: str = "ru") -> str:
    try:
        results = aggregated_search(query=query, source="all", lang=lang)
        return "\n\n".join(results)
    except Exception as e:
        logger.warning("⚠️ Ошибка внешнего контекста: %s", e)
        return ""


def render_prompt(template_name: str, **kwargs) -> str:
    try:
        template = env.get_template(template_name)
    except TemplateNotFound:
        raise HTTPException(400, f"Шаблон '{template_name}' не найден")
    return template.render(**kwargs)


# -------------------- Public API --------------------
def generate_from_prompt(
    template_name: str | None = None,
    *,
    prompt: str | None = None,
    engine: str = DEFAULT_ENGINE,
    model_name: str | None = None,
    include_external_context: bool = True,
    use_feedback: bool = True,
    lang: str = "ru",
    db: Any = None,
    expect_json: bool = True,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Унифицированный вызов LLM.

    - template_name: имя jinja2-шаблона (app/prompts)
    - prompt: прямой текст промпта (если template_name не указан)
    - engine/model_name: выбор движка/модели
    - include_external_context: вставлять aggregated_search
    - use_feedback: добавлять summary отзывов (если есть db и lesson_id)
    - expect_json: пытаться парсить ответ как JSON
    - max_tokens: лимит токенов для генерации (если клиент поддерживает)
    """

    # ---- нормализуем движок ----
    engine = ENGINE_ALIASES.get(engine, engine)
    if engine not in SUPPORTED_ENGINES:
        raise HTTPException(400, f"❌ Неподдерживаемый движок: {engine}")

    # ---- формируем prompt ----
    if template_name:
        if include_external_context:
            cq = kwargs.get("course_name") or kwargs.get("lesson_title")
            kwargs["external_context"] = get_cached_external_context(cq, lang) if cq else ""
        else:
            kwargs["external_context"] = ""

        if use_feedback and db is not None and "lesson_id" in kwargs:
            try:
                kwargs["feedback_context"] = get_feedback_summary(kwargs["lesson_id"], db)
            except Exception as e:
                logger.warning("⚠️ Ошибка feedback_context: %s", e)
                kwargs["feedback_context"] = ""
        else:
            kwargs["feedback_context"] = ""

        final_prompt = render_prompt(template_name, **kwargs)
    else:
        final_prompt = prompt or kwargs.get("prompt")
        if not final_prompt:
            raise HTTPException(400, "Не передан template_name и отсутствует prompt")

    logger.info("📤 Prompt:\n%s", final_prompt)

    # ---- инициализируем клиента ----
    try:
        ctor = SUPPORTED_ENGINES[engine]
        client, used_model = ctor(model_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "❌ Ошибка инициализации движка %s: %s\n%s",
            engine,
            e,
            traceback.format_exc(),
        )
        raise HTTPException(500, f"Ошибка инициализации LLM: {e}")

    # ---- вызов модели ----
    try:
        try:
            raw = client.generate(final_prompt, max_tokens=max_tokens)
        except TypeError:
            # сигнатура без max_tokens
            raw = client.generate(final_prompt)
    except Exception as e:
        logger.error("❌ Ошибка вызова LLM: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"Ошибка при обращении к модели: {e}")

    raw_clean = (raw or "").strip()
    logger.info("📥 Raw output (%s):\n%s", used_model, raw_clean)

    if not expect_json:
        return {"text": raw_clean, "model": used_model}

    try:
        res = _safe_json_loads(raw_clean)
        res["_model"] = used_model
        return res
    except HTTPException:
        raise
    except Exception:
        logger.error("❌ Ошибка парсинга JSON:\n%s", traceback.format_exc())
        raise HTTPException(500, "Ошибка разбора JSON от LLM")


def list_available_models() -> list[str]:
    """Возвращает объединённый список доступных моделей (HF + GigaChat)."""
    try:
        hf = get_available_hf_models()
    except Exception as e:
        logger.warning("HF list error: %s", e)
        hf = []
    try:
        gg = get_available_giga_models()
    except Exception as e:
        logger.warning("GigaChat list error: %s", e)
        gg = []
    return hf + gg
