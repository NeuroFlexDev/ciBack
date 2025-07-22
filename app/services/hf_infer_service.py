import logging
import os
from functools import lru_cache

import requests
from fastapi import HTTPException
from huggingface_hub import InferenceClient

from app.services.llm_types import LLMClient

logger = logging.getLogger(__name__)

HF_MODELS_API = "https://huggingface.co/api/models"
DISCOVERY_LIMIT = 60
DISCOVERY_TIMEOUT = 6
PING_PROMPT = "ping"


# -------------------- Wrappers --------------------
class HFClientWrapper(LLMClient):
    def __init__(self, inner: InferenceClient):
        self.inner = inner

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        return self.inner.text_generation(
            prompt, max_new_tokens=max_tokens, timeout=DISCOVERY_TIMEOUT + 4
        )


# -------------------- Helpers --------------------
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
        r = requests.get(HF_MODELS_API, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        models = r.json()
    except Exception as e:
        logger.warning("HF list models failed: %s", e)
        return []
    result = []
    for m in models:
        if m.get("disabled") or m.get("private"):
            continue
        mid = m.get("modelId") or m.get("id")
        if mid:
            result.append(mid)
    return result


def _probe_model(model: str, token: str, api_url_env: str | None) -> bool:
    try:
        client = _build_client(model, token, api_url_env)
        client.text_generation(PING_PROMPT, max_new_tokens=4, timeout=DISCOVERY_TIMEOUT)
        return True
    except Exception as e:
        logger.debug("Probe fail %s: %s", model, e)
        return False


@lru_cache(maxsize=1)
def _discover_available_models(token: str, api_url_env: str | None) -> list[str]:
    env_model = os.getenv("HF_MODEL")
    env_list = os.getenv("HF_MODEL_CANDIDATES", "")
    from_env = [m.strip() for m in env_list.split(",") if m.strip()]

    candidates = []
    if env_model:
        candidates.append(env_model)
    candidates.extend(from_env)

    fetched = _hf_list_text_gen_models(token, DISCOVERY_LIMIT)
    for m in fetched:
        if m not in candidates:
            candidates.append(m)

    logger.info("HF discovery candidates: %d", len(candidates))
    available: list[str] = []
    for m in candidates:
        if _probe_model(m, token, api_url_env):
            available.append(m)

    logger.info("HF available: %d", len(available))
    return available


# -------------------- Public API --------------------
def get_hf_client(preferred_model: str | None = None) -> tuple[LLMClient, str]:
    token = os.getenv("HF_TOKEN")
    if not token:
        raise HTTPException(500, "HF_TOKEN не настроен")

    api_url_env = os.getenv("HF_API_URL")

    available = _discover_available_models(token, api_url_env)
    if not available:
        raise HTTPException(500, "Нет доступных моделей на HuggingFace Inference")

    used = preferred_model if preferred_model in available else available[0]
    client = _build_client(used, token, api_url_env)
    return HFClientWrapper(client), used


def get_available_hf_models() -> list[str]:
    token = os.getenv("HF_TOKEN")
    if not token:
        return []
    api_url_env = os.getenv("HF_API_URL")
    return _discover_available_models(token, api_url_env)
