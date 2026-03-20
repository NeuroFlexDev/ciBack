from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from app.services.external_sources import aggregated_search
from app.services.feedback_service import get_feedback_summary
from app.services.llm_registry import (
    DEFAULT_ENGINE,
    ENGINE_ALIASES,
    PROVIDER_FACTORIES,
    invoke_llm,
)
from app.services.llm_types import LLMClient, LLMError
from log import get_logger


env = Environment(loader=FileSystemLoader("app/prompts"))
logger = get_logger(__name__)

SUPPORTED_ENGINES: dict[str, Callable[[str | None], tuple[LLMClient, str]]] = PROVIDER_FACTORIES
DEFAULT_MAX_TOKENS = 1024
DEFAULT_MODEL: str | None = None

JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def course_kwargs(course, cs=None):
    data = {
        "course_name": course.name,
        "course_description": course.description,
        "course_level": course.level,
    }
    if cs:
        data.update(
            {
                "module_count": cs.sections,
                "lessons_per_section": cs.lessons_per_section,
            }
        )
    return data


def _safe_json_loads(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    candidates = JSON_BLOCK_RE.findall(cleaned)
    candidates.sort(key=len, reverse=True)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            fixed = re.sub(r"(?<!\\)'", '"', candidate)
            try:
                return json.loads(fixed)
            except Exception:
                continue

    raise HTTPException(500, "Failed to parse JSON returned by LLM")


def _map_llm_error(exc: LLMError) -> HTTPException:
    logger.error(
        "llm request failed provider=%s model=%s code=%s retryable=%s",
        exc.provider,
        exc.model,
        exc.code,
        exc.retryable,
    )
    return HTTPException(exc.status_code, exc.message)


@lru_cache(maxsize=128)
def get_cached_external_context(query: str, lang: str = "ru") -> str:
    try:
        results = aggregated_search(query=query, source="all", lang=lang)
        return "\n\n".join(results)
    except Exception as exc:
        logger.warning("external context fetch failed error=%s", exc.__class__.__name__)
        return ""


def render_prompt(template_name: str, **kwargs) -> str:
    try:
        template = env.get_template(template_name)
    except TemplateNotFound as exc:
        raise HTTPException(400, f"Template '{template_name}' not found") from exc
    return template.render(**kwargs)


def llm_json(template: str, *, engine: str, model: str | None, **kwargs) -> dict[str, Any]:
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("llm_json failed error=%s", exc.__class__.__name__)
        raise HTTPException(500, f"LLM generation failed: {exc}") from exc


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
    engine = ENGINE_ALIASES.get(engine, engine)
    if engine not in SUPPORTED_ENGINES:
        raise HTTPException(400, f"Unsupported LLM engine: {engine}")

    if template_name:
        if include_external_context:
            context_query = kwargs.get("course_name") or kwargs.get("lesson_title")
            kwargs["external_context"] = get_cached_external_context(context_query, lang) if context_query else ""
        else:
            kwargs["external_context"] = ""

        if use_feedback and db is not None and "lesson_id" in kwargs:
            try:
                kwargs["feedback_context"] = get_feedback_summary(kwargs["lesson_id"], db)
            except Exception as exc:
                logger.warning("feedback context failed error=%s", exc.__class__.__name__)
                kwargs["feedback_context"] = ""
        else:
            kwargs["feedback_context"] = ""

        final_prompt = render_prompt(template_name, **kwargs)
    else:
        final_prompt = prompt or kwargs.get("prompt")
        if not final_prompt:
            raise HTTPException(400, "Either template_name or prompt must be provided")

    logger.info(
        "llm prompt prepared provider=%s model=%s template=%s chars=%s external_context=%s feedback=%s",
        engine,
        model_name or "-",
        template_name or "<direct>",
        len(final_prompt),
        include_external_context,
        use_feedback,
    )

    try:
        raw, meta = invoke_llm(
            final_prompt,
            engine=engine,
            model=model_name,
            max_tokens=max_tokens,
            engines=SUPPORTED_ENGINES,
        )
    except HTTPException:
        raise
    except LLMError as exc:
        raise _map_llm_error(exc) from exc

    raw_clean = (raw or "").strip()
    logger.info(
        "llm invocation succeeded provider=%s model=%s latency_ms=%s attempts=%s tokens=%s",
        meta.provider,
        meta.model,
        meta.latency_ms,
        meta.attempts,
        meta.token_usage or {},
    )

    if not expect_json:
        return {
            "text": raw_clean,
            "model": meta.model,
            "provider": meta.provider,
            "latency_ms": meta.latency_ms,
            "attempts": meta.attempts,
        }

    try:
        result = _safe_json_loads(raw_clean)
        result["_model"] = meta.model
        result["_provider"] = meta.provider
        result["_latency_ms"] = meta.latency_ms
        result["_attempts"] = meta.attempts
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "llm json parsing failed provider=%s model=%s error=%s",
            meta.provider,
            meta.model,
            exc.__class__.__name__,
        )
        raise HTTPException(500, "Failed to parse JSON returned by LLM") from exc


def list_available_models() -> list[str]:
    from app.services.llm_registry import list_models

    try:
        return list_models()
    except Exception as exc:
        logger.warning("llm model listing failed error=%s", exc.__class__.__name__)
        return []
