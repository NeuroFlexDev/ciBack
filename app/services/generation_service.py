from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.services.external_sources import aggregated_search
from app.services.feedback_service import get_feedback_summary
from app.services.gigachat_service import get_gigachat_client
from app.services.hf_infer_service import get_hf_client
from app.services.llm_types import LLMClient


env = Environment(loader=FileSystemLoader("app/prompts"))
logger = logging.getLogger(__name__)

# Keep the historic default name public while routing it to the canonical
# provider below.
DEFAULT_ENGINE = "huggingface"
ENGINE_ALIASES = {
    "huggingface": "hf_api",
    "lc_giga": "gigachat",
}
_DEFAULT_ENGINE_FACTORIES: dict[str, Callable[..., Any]] = {
    "hf_api": get_hf_client,
    "gigachat": get_gigachat_client,
    "huggingface": get_hf_client,
    "lc_giga": get_gigachat_client,
}
SUPPORTED_ENGINES: dict[str, Callable[..., Any]] = {
    **_DEFAULT_ENGINE_FACTORIES,
}

MAX_INPUT_TOKENS = 4000
MAX_OUTPUT_TOKENS = 1024

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)


@lru_cache(maxsize=128)
def get_cached_external_context(query: str, lang: str = "ru") -> str:
    try:
        results = aggregated_search(query=query, source="all", lang=lang)
        return "\n\n".join(results)
    except Exception as exc:
        logger.warning(
            "External context fetch failed error=%s", exc.__class__.__name__
        )
        return ""


def render_prompt(template_name: str, **kwargs: Any) -> str:
    """Render a configured Jinja prompt template."""
    template = env.get_template(template_name)
    return template.render(**kwargs)


def _call_factory(factory: Callable[..., Any], model: str | None) -> Any:
    """Call both legacy no-argument and model-aware provider factories."""
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        # Provider factories are normal Python callables, but keep a sensible
        # fallback for opaque callables whose signature cannot be inspected.
        return factory() if model is None else factory(model)

    try:
        signature.bind(model)
    except TypeError:
        try:
            signature.bind(model=model)
        except TypeError:
            try:
                signature.bind()
            except TypeError as exc:
                raise TypeError(
                    "LLM factory must accept either model or no arguments"
                ) from exc
            return factory()
        return factory(model=model)
    return factory(model)


def _get_factory(engine: str, resolved_engine: str) -> Callable[..., Any] | None:
    """Resolve aliases while honoring tests and legacy custom registrations.

    Historically callers replaced a factory under the alias key itself. A
    custom alias registration therefore wins; otherwise aliases use the
    canonical provider entry.
    """
    alias_factory = SUPPORTED_ENGINES.get(engine)
    if (
        engine != resolved_engine
        and alias_factory is not None
        and alias_factory is not _DEFAULT_ENGINE_FACTORIES.get(engine)
    ):
        return alias_factory
    return SUPPORTED_ENGINES.get(resolved_engine)


def _create_client(
    factory: Callable[..., Any], model: str | None
) -> tuple[LLMClient, str | None]:
    created = _call_factory(factory, model)
    if isinstance(created, tuple):
        if len(created) != 2:
            raise TypeError("LLM factory tuple must contain client and used model")
        client, used_model = created
    else:
        client = created
        used_model = getattr(client, "model", None)

    if not hasattr(client, "generate"):
        raise TypeError("LLM factory returned an invalid client")
    return client, str(used_model) if used_model is not None else None


def _json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip().lstrip("\ufeff")
    fenced = _JSON_FENCE.search(cleaned)
    if fenced is not None:
        cleaned = fenced.group(1).strip()

    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError) as exc:
        logger.warning("LLM JSON parsing failed error=%s", exc.__class__.__name__)
        raise HTTPException(500, "Ошибка разбора JSON от LLM") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(500, "LLM должен вернуть JSON-объект")
    return parsed


def generate_from_prompt(
    template_name: str | None = None,
    *,
    prompt: str | None = None,
    engine: str = DEFAULT_ENGINE,
    model: str | None = None,
    expect_json: bool = True,
    include_external_context: bool = True,
    use_feedback: bool = True,
    lang: str = "ru",
    db: Session | None = None,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    **kwargs: Any,
) -> dict[str, Any]:
    """Invoke an LLM and return a stable JSON-shaped response.

    Calls may provide either a Jinja ``template_name`` or a direct ``prompt``.
    Legacy provider factories that take no arguments and newer factories that
    accept a preferred model are both supported.
    """
    resolved_engine = ENGINE_ALIASES.get(engine, engine)
    factory = _get_factory(engine, resolved_engine)
    if factory is None:
        raise HTTPException(400, f"Неподдерживаемый движок генерации: {engine}")

    if template_name is not None:
        if include_external_context:
            context_query = kwargs.get("course_name") or kwargs.get("lesson_title")
            kwargs["external_context"] = (
                get_cached_external_context(context_query, lang=lang)
                if context_query
                else ""
            )
        else:
            kwargs["external_context"] = ""

        if use_feedback and "lesson_id" in kwargs and db is not None:
            kwargs["feedback_context"] = get_feedback_summary(kwargs["lesson_id"], db)

        final_prompt = render_prompt(template_name, **kwargs)
    else:
        final_prompt = prompt

    if not final_prompt:
        raise HTTPException(400, "Нужно передать template_name или prompt")

    logger.info(
        "LLM request prepared engine=%s model=%s template=%s prompt_chars=%d",
        resolved_engine,
        model or "-",
        template_name or "<direct>",
        len(final_prompt),
    )

    llm_client, used_model = _create_client(factory, model)
    raw = llm_client.generate(final_prompt, max_tokens=max_tokens)

    if isinstance(raw, dict) and "text" not in raw:
        result = dict(raw)
        response_chars = len(json.dumps(result, ensure_ascii=False))
    else:
        raw_text = raw.get("text", "") if isinstance(raw, dict) else str(raw or "")
        response_chars = len(raw_text)
        if expect_json:
            result = _json_object(raw_text)
        else:
            result = {"text": raw_text.strip()}

    # Metadata keys are reserved for the gateway. Never trust a model-supplied
    # value, and only expose one when the factory/client identified it.
    result.pop("_model", None)
    if used_model is not None:
        result["_model"] = used_model

    logger.info(
        "LLM request succeeded engine=%s model=%s response_chars=%d",
        resolved_engine,
        used_model or "-",
        response_chars,
    )
    return result
