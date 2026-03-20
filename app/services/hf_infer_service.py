from __future__ import annotations

from functools import lru_cache

import requests
from huggingface_hub import InferenceClient

from app.core.config import settings
from app.services.llm_types import LLMClient, LLMError, LLMInvocationMeta
from log import get_logger

logger = get_logger(__name__)

HF_MODELS_API = "https://huggingface.co/api/models"
PING_PROMPT = "ping"


class HFClientWrapper(LLMClient):
    def __init__(self, inner: InferenceClient, *, model: str):
        self.inner = inner
        self.model = model
        self.last_invocation = LLMInvocationMeta(provider="hf_api", model=model)

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        try:
            result = self.inner.text_generation(
                prompt,
                max_new_tokens=max_tokens,
                timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            raise LLMError(
                code="timeout",
                message="Hugging Face request timed out",
                provider="hf_api",
                model=self.model,
                status_code=504,
                retryable=True,
            ) from exc
        except Exception as exc:
            error_text = f"{exc.__class__.__name__}: {exc}".lower()
            if "timeout" in error_text:
                raise LLMError(
                    code="timeout",
                    message="Hugging Face request timed out",
                    provider="hf_api",
                    model=self.model,
                    status_code=504,
                    retryable=True,
                ) from exc
            raise LLMError(
                code="provider_error",
                message="Hugging Face request failed",
                provider="hf_api",
                model=self.model,
                status_code=503,
                retryable=False,
                details={"error_type": exc.__class__.__name__},
            ) from exc

        self.last_invocation = LLMInvocationMeta(provider="hf_api", model=self.model)
        return str(result)


def _build_client(model: str, token: str, api_url: str | None) -> InferenceClient:
    if api_url:
        return InferenceClient(api_url=api_url, token=token)
    return InferenceClient(model=model, token=token)


def _hf_list_text_gen_models(token: str, limit: int) -> list[str]:
    params = {
        "pipeline_tag": "text-generation",
        "sort": "downloads",
        "direction": -1,
        "limit": limit,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = requests.get(
            HF_MODELS_API,
            params=params,
            headers=headers,
            timeout=settings.HF_DISCOVERY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        models = response.json()
    except Exception as exc:
        logger.warning("hf model discovery failed error=%s", exc.__class__.__name__)
        return []

    result = []
    for model in models:
        if model.get("disabled") or model.get("private"):
            continue
        model_id = model.get("modelId") or model.get("id")
        if model_id:
            result.append(model_id)
    return result


def _probe_model(model: str, token: str, api_url: str | None) -> bool:
    try:
        client = _build_client(model, token, api_url)
        client.text_generation(
            PING_PROMPT,
            max_new_tokens=4,
            timeout=settings.HF_DISCOVERY_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:
        logger.debug("hf model probe failed model=%s error=%s", model, exc.__class__.__name__)
        return False


@lru_cache(maxsize=8)
def _discover_available_models(token: str, api_url: str, env_model: str, env_candidates: str) -> list[str]:
    from_env = [value.strip() for value in env_candidates.split(",") if value.strip()]

    candidates: list[str] = []
    if env_model:
        candidates.append(env_model)
    candidates.extend(from_env)

    fetched = _hf_list_text_gen_models(token, settings.HF_DISCOVERY_LIMIT)
    for model in fetched:
        if model not in candidates:
            candidates.append(model)

    logger.info("hf discovery candidates=%s", len(candidates))
    available = [model for model in candidates if _probe_model(model, token, api_url or None)]
    logger.info("hf discovery available=%s", len(available))
    return available


def get_hf_client(preferred_model: str | None = None) -> tuple[LLMClient, str]:
    if not settings.HF_TOKEN:
        raise LLMError(
            code="configuration_error",
            message="HF_TOKEN is not configured",
            provider="hf_api",
            model=preferred_model,
            status_code=503,
            retryable=False,
        )

    available = _discover_available_models(
        settings.HF_TOKEN,
        settings.HF_API_URL,
        settings.HF_MODEL,
        settings.HF_MODEL_CANDIDATES,
    )
    if not available:
        raise LLMError(
            code="configuration_error",
            message="No available Hugging Face inference models found",
            provider="hf_api",
            model=preferred_model,
            status_code=503,
            retryable=False,
        )

    used_model = preferred_model if preferred_model in available else available[0]
    client = _build_client(used_model, settings.HF_TOKEN, settings.HF_API_URL or None)
    return HFClientWrapper(client, model=used_model), used_model


def get_available_hf_models() -> list[str]:
    if not settings.HF_TOKEN:
        return []
    try:
        return _discover_available_models(
            settings.HF_TOKEN,
            settings.HF_API_URL,
            settings.HF_MODEL,
            settings.HF_MODEL_CANDIDATES,
        )
    except Exception as exc:
        logger.warning("hf available models failed error=%s", exc.__class__.__name__)
        return []
