from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from langchain_core.runnables import RunnableLambda

from app.core.config import settings
from app.services.gigachat_service import get_available_giga_models, get_gigachat_client
from app.services.hf_infer_service import get_available_hf_models, get_hf_client
from app.services.llm_types import LLMClient, LLMError, LLMInvocationMeta
from log import get_logger

logger = get_logger(__name__)

PROVIDER_FACTORIES: dict[str, Callable[[str | None], tuple[LLMClient, str]]] = {
    "hf_api": get_hf_client,
    "gigachat": get_gigachat_client,
}

ENGINE_ALIASES: dict[str, str] = {
    "lc_giga": "gigachat",
    "lc_gc": "gigachat",
    "lc_hf": "hf_api",
}

DEFAULT_ENGINE = "gigachat"


def resolve_engine(engine: str | None) -> str:
    requested = (engine or DEFAULT_ENGINE).strip()
    resolved = ENGINE_ALIASES.get(requested, requested)
    if resolved not in PROVIDER_FACTORIES:
        raise HTTPException(400, f"Unsupported LLM engine: {resolved}")
    return resolved


def _call_client_generate(client: LLMClient, prompt: str, max_tokens: int) -> str:
    signature = inspect.signature(client.generate)
    if "max_tokens" in signature.parameters:
        return client.generate(prompt, max_tokens=max_tokens)
    return client.generate(prompt)


def _normalize_unexpected_error(exc: Exception, *, provider: str, model: str | None) -> LLMError:
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if "timeout" in lowered:
        return LLMError(
            code="timeout",
            message="LLM request timed out",
            provider=provider,
            model=model,
            status_code=504,
            retryable=True,
        )
    return LLMError(
        code="provider_error",
        message="LLM provider request failed",
        provider=provider,
        model=model,
        status_code=503,
        retryable=False,
        details={"error_type": exc.__class__.__name__},
    )


def invoke_client(
    client: LLMClient,
    *,
    provider: str,
    model: str,
    prompt: str,
    max_tokens: int,
    retry_attempts: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> tuple[str, LLMInvocationMeta]:
    total_attempts = max(1, retry_attempts or settings.LLM_RETRY_ATTEMPTS)
    backoff = settings.LLM_RETRY_BACKOFF_SECONDS if retry_backoff_seconds is None else retry_backoff_seconds
    last_error: LLMError | None = None

    for attempt in range(1, total_attempts + 1):
        started_at = time.perf_counter()
        try:
            raw = _call_client_generate(client, prompt, max_tokens)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            meta = getattr(client, "last_invocation", None)
            if isinstance(meta, LLMInvocationMeta):
                meta.provider = provider
                meta.model = model
                meta.latency_ms = elapsed_ms if meta.latency_ms <= 0 else meta.latency_ms
                meta.attempts = attempt
            else:
                meta = LLMInvocationMeta(
                    provider=provider,
                    model=model,
                    latency_ms=elapsed_ms,
                    attempts=attempt,
                )
            return raw, meta
        except LLMError as exc:
            last_error = exc.with_context(provider=provider, model=model)
        except HTTPException as exc:
            last_error = LLMError(
                code="provider_http_error",
                message=str(exc.detail),
                provider=provider,
                model=model,
                status_code=exc.status_code,
                retryable=exc.status_code >= 500,
            )
        except Exception as exc:
            last_error = _normalize_unexpected_error(exc, provider=provider, model=model)

        logger.warning(
            "llm invocation failed provider=%s model=%s attempt=%s/%s code=%s retryable=%s",
            provider,
            model,
            attempt,
            total_attempts,
            last_error.code,
            last_error.retryable,
        )
        if attempt >= total_attempts or not last_error.retryable:
            raise last_error
        if backoff > 0:
            time.sleep(backoff * attempt)

    assert last_error is not None
    raise last_error


def invoke_llm(
    prompt: str,
    *,
    engine: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    engines: dict[str, Callable[[str | None], tuple[LLMClient, str]]] | None = None,
) -> tuple[str, LLMInvocationMeta]:
    resolved_engine = resolve_engine(engine)
    registry = engines or PROVIDER_FACTORIES
    if resolved_engine not in registry:
        raise HTTPException(400, f"Unsupported LLM engine: {resolved_engine}")
    total_attempts = max(1, settings.LLM_RETRY_ATTEMPTS)
    last_error: LLMError | None = None

    for attempt in range(1, total_attempts + 1):
        try:
            client, used_model = registry[resolved_engine](model)
            raw, meta = invoke_client(
                client,
                provider=resolved_engine,
                model=used_model,
                prompt=prompt,
                max_tokens=max_tokens,
                retry_attempts=1,
            )
            meta.attempts = attempt
            return raw, meta
        except LLMError as exc:
            last_error = exc.with_context(provider=resolved_engine, model=model)
        except HTTPException as exc:
            last_error = LLMError(
                code="provider_http_error",
                message=str(exc.detail),
                provider=resolved_engine,
                model=model,
                status_code=exc.status_code,
                retryable=exc.status_code >= 500,
            )
        except Exception as exc:
            last_error = _normalize_unexpected_error(exc, provider=resolved_engine, model=model)

        logger.warning(
            "llm provider setup failed provider=%s model=%s attempt=%s/%s code=%s retryable=%s",
            resolved_engine,
            model or "-",
            attempt,
            total_attempts,
            last_error.code,
            last_error.retryable,
        )
        if attempt >= total_attempts or not last_error.retryable:
            raise last_error
        if settings.LLM_RETRY_BACKOFF_SECONDS > 0:
            time.sleep(settings.LLM_RETRY_BACKOFF_SECONDS * attempt)

    assert last_error is not None
    raise last_error


def _wrap_engine_as_runnable(engine: str, model: str | None = None, max_tokens: int = 1024):
    def _run(inp: Any) -> str:
        if isinstance(inp, dict):
            prompt = inp.get("question") or inp.get("input") or inp.get("prompt") or ""
        else:
            prompt = str(inp)
        raw, _meta = invoke_llm(
            prompt,
            engine=engine,
            model=model,
            max_tokens=max_tokens,
        )
        return raw

    return RunnableLambda(_run)


def get_llm(model: str | None = None, engine: str | None = None):
    resolved_engine = resolve_engine(engine)
    return _wrap_engine_as_runnable(resolved_engine, model=model)


def list_models() -> list[str]:
    return get_available_hf_models() + get_available_giga_models()
