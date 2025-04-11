import json
import traceback
import logging
from functools import lru_cache
from jinja2 import Environment, FileSystemLoader
from fastapi import HTTPException

from app.services.huggingface_service import get_hugchat
from app.services.external_sources import aggregated_search
from app.services.feedback_service import get_feedback_summary

env = Environment(loader=FileSystemLoader("app/prompts"))

# === Конфигурация движков ===
DEFAULT_ENGINE = "huggingface"
SUPPORTED_ENGINES = {
    "huggingface": get_hugchat,
    # В будущем можно добавить другие, например: "ollama": get_ollama_client
}

# === Ограничения по токенам ===
MAX_INPUT_TOKENS = 4000
MAX_OUTPUT_TOKENS = 1024

logger = logging.getLogger(__name__)

@lru_cache(maxsize=128)
def get_cached_external_context(query: str, lang: str = "ru") -> str:
    try:
        results = aggregated_search(query=query, source="all", lang=lang)
        return "\n\n".join(results)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка получения внешнего контекста: {str(e)}")
        return ""

def render_prompt(template_name: str, **kwargs) -> str:
    """Рендерит шаблон промта."""
    template = env.get_template(template_name)
    return template.render(**kwargs)

def generate_from_prompt(template_name: str, engine: str = DEFAULT_ENGINE, include_external_context: bool = True, use_feedback=True, lang: str = "ru", **kwargs) -> dict:
    """
    Генерация через LLM с шаблоном Jinja и расширенным контекстом.
    Параметры:
        - template_name: имя шаблона .j2
        - engine: имя LLM-движка (по умолчанию huggingface)
        - include_external_context: добавлять ли внешний контекст из поисковиков
        - lang: язык запроса (по умолчанию ru)
    """
    if engine not in SUPPORTED_ENGINES:
        raise HTTPException(400, f"❌ Неподдерживаемый движок генерации: {engine}")

    if include_external_context:
        context_query = kwargs.get("course_name") or kwargs.get("lesson_title")
        if context_query:
            kwargs["external_context"] = get_cached_external_context(context_query, lang=lang)
        else:
            kwargs["external_context"] = ""

    if use_feedback and "lesson_id" in kwargs and db:
        feedback_context = get_feedback_summary(kwargs["lesson_id"], db)
        kwargs["feedback_context"] = feedback_context
        
    prompt = render_prompt(template_name, **kwargs)
    logger.info(f"📤 Prompt:\n{prompt}")

    llm_client = SUPPORTED_ENGINES[engine]()
    resp = llm_client.chat(prompt)
    raw = resp.wait_until_done()

    raw = raw.strip().strip("```json").strip("```")
    logger.info(f"📥 Raw JSON:\n{raw}")

    try:
        return json.loads(raw)
    except Exception:
        logger.error("❌ Ошибка парсинга JSON:")
        logger.error(traceback.format_exc())
        raise HTTPException(500, "Ошибка разбора JSON от LLM")
