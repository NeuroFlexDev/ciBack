import base64
import logging
import os
import time
import uuid

import requests
from fastapi import HTTPException

from app.services.llm_types import LLMClient

logger = logging.getLogger(__name__)

GIGA_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGA_API_BASE = "https://gigachat.devices.sberbank.ru/api/v1"

_token_cache = {"val": None, "exp": 0}


class GigaChatClient(LLMClient):
    def __init__(self, model: str, scope: str = "GIGACHAT_API_PERS"):
        self.model = model
        self.scope = scope

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        token = _get_access_token(self.scope)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "n": 1,
            "stream": False,
            "repetition_penalty": 1.0,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            r = requests.post(
                f"{GIGA_API_BASE}/chat/completions",
                json=payload,
                headers=headers,
                timeout=40,
                verify=True,
            )
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(500, f"GigaChat request failed: {e}")
        js = r.json()
        return js["choices"][0]["message"]["content"]


# -------------------- helpers --------------------
def _basic_header() -> str:
    cid = os.getenv("GIGA_CLIENT_ID")
    csec = os.getenv("GIGA_CLIENT_SECRET")
    if not cid or not csec:
        raise HTTPException(500, "GIGA_CLIENT_ID/GIGA_CLIENT_SECRET не заданы")
    # секрет должен быть НЕ base64; если ты сохранил уже b64, сначала декодни
    if ":" in csec:
        cred = f"{cid}:{csec}"
    else:
        # предполагаем что GIGA_CLIENT_SECRET — чистый секрет без id
        cred = f"{cid}:{csec}"
    return base64.b64encode(cred.encode()).decode()


def _get_access_token(scope: str) -> str:
    now = time.time()
    if _token_cache["val"] and now < _token_cache["exp"] - 60:
        return _token_cache["val"]

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {_basic_header()}",
    }
    data = {"scope": scope}
    try:
        r = requests.post(GIGA_OAUTH_URL, headers=headers, data=data, timeout=15, verify=True)
        r.raise_for_status()
    except requests.HTTPError as e:
        raise HTTPException(
            500,
            f"GigaChat token HTTP error: {e.response.status_code} {e.response.text}",
        )
    except Exception as e:
        raise HTTPException(500, f"GigaChat token request failed: {e}")

    js = r.json()
    token = js["access_token"]
    exp_ms = js.get("expires_at", int((now + 1700) * 1000))
    _token_cache["val"] = token
    _token_cache["exp"] = exp_ms / 1000
    return token


def list_gigachat_models(scope: str | None = None) -> list[str]:
    token = _get_access_token(scope or os.getenv("GIGA_SCOPE", "GIGACHAT_API_PERS"))
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{GIGA_API_BASE}/models", headers=headers, timeout=15, verify=True)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(500, f"Ошибка получения списка моделей GigaChat: {e}")
    js = r.json()
    # Ответ может быть {"data": [...]} или просто list
    if isinstance(js, dict) and "data" in js:
        models = js["data"]
    else:
        models = js
    ids = []
    for m in models:
        mid = m.get("id") or m.get("model")
        if mid:
            ids.append(mid)
    return ids


def get_gigachat_client(preferred_model: str | None = None) -> tuple[LLMClient, str]:
    models = list_gigachat_models()
    if not models:
        raise HTTPException(500, "Нет доступных GigaChat моделей")
    used = preferred_model if preferred_model in models else models[0]
    return GigaChatClient(used, scope=os.getenv("GIGA_SCOPE", "GIGACHAT_API_PERS")), used


def get_available_giga_models() -> list[str]:
    try:
        return list_gigachat_models()
    except Exception:
        return []
