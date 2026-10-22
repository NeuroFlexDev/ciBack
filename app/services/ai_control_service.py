"""Operator-only management. Secrets never leave the credential resolver."""
import json
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy.exc import IntegrityError
from app.ai import control, providers
from app.ai.managed_http import client as http_client, destination
from app.ai.models import model_policy
from app.ai.pricing import candidate_price
from app.core.config import AIProvider, AIRoutingCandidate, settings
from app.models.ai_control import AIControl
from app.models.ai_state import AICall
from app.models.generation_run import GenerationRun
from app.services.integration_secrets import seal
from app.services.organization_access import audit

ROLES = {"ingestion": "Разбор источников", "competency_mapper": "Компетенции",
         "course_architect": "Структура курса", "lesson_writer": "Написание уроков",
         "assessment": "Задания и тесты", "critic_qa": "Проверка качества",
         "update": "Обновление курса", "chat": "Ответы помощника",
         "memory": "Память диалога", "vision": "Анализ изображений", "canvas": "Редактор курса"}
CATALOG = [
    {"id": "vsellm", "name": "VSELLM", "base_url": "https://api.vsellm.ru/v1", "purpose": "Текущий основной канал и оплата в рублях", "docs": "https://vsellm.ru/", "local": False},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "purpose": "Резервный агрегатор и широкий выбор моделей", "docs": "https://openrouter.ai/docs/quickstart", "local": False},
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com", "purpose": "Прямой канал для сравнения стоимости генерации", "docs": "https://api-docs.deepseek.com/quick_start/pricing/", "local": False},
    {"id": "gemini", "name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "purpose": "Мультимодальность и независимый резерв", "docs": "https://ai.google.dev/gemini-api/docs/openai", "local": False},
    {"id": "yandex", "name": "Yandex AI Studio", "base_url": "https://ai.api.cloud.yandex.net/v1", "purpose": "Российский облачный провайдер для корпоративных проектов", "docs": "https://aistudio.yandex.ru/ru/docs/ai-studio/operations/generation/completions-basic", "local": False},
    {"id": "local", "name": "Ollama / vLLM", "base_url": "", "purpose": "Модели в собственном контуре и локальный резерв", "docs": "https://docs.ollama.com/api/openai-compatibility", "local": True},
]


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class ProviderChange(Revision):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=1000)
    local: bool = False
    enabled: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    project: str = Field(default="", max_length=128, pattern=r"^[a-zA-Z0-9_-]*$")
    key_action: Literal["keep", "replace", "remove"] = "keep"
    api_key: SecretStr | None = None


class ModelChange(AIRoutingCandidate):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_route(self):
        if any(role not in ROLES for role in self.roles) or len(set(self.roles)) != len(self.roles):
            raise ValueError("Выберите существующие этапы без повторов.")
        if max(self.max_input_tokens, self.max_output_tokens) > self.context_window:
            raise ValueError("Лимиты входа и ответа не должны превышать контекст модели.")
        if not self.model.strip() or any(ord(c) < 32 for c in self.model):
            raise ValueError("Нужен идентификатор модели из каталога провайдера.")
        if self.approved and (not self.json_mode or "critic_qa" in self.roles and not self.independent_review or "vision" in self.roles and not self.vision):
            raise ValueError("Включите JSON и нужные возможности модели для выбранных этапов.")
        allowed = {"provider", "thinking", "reasoning", "google", "chat_template_kwargs"}
        if set(self.extra_body) - allowed or len(json.dumps(self.extra_body)) > 4096:
            raise ValueError("Допустимы параметры provider, thinking, reasoning, google, chat_template_kwargs (до 4 КБ).")
        json.dumps(self.extra_body, allow_nan=False)
        return self


class PolicyChange(Revision):
    mode: Literal["adaptive"] = "adaptive"
    local_only: bool = False
    min_samples: int = Field(default=10, ge=3, le=1000)
    min_pass_rate: float = Field(default=.85, ge=.5, le=1, allow_inf_nan=False)


def row_for(db, revision=None):
    row = db.get(AIControl, 1)
    if row is None:
        row = AIControl(id=1, revision=0, providers=[], candidates=[], policy={})
        try:
            db.add(row)
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Настройки изменились. Обновите страницу.") from None
    if revision is not None and row.revision != revision:
        raise HTTPException(409, "Настройки изменены другим оператором. Обновите страницу и проверьте изменения.")
    return row


def save(db, actor, row, action, target, **changes):
    revision = row.revision
    affected = db.query(AIControl).filter(AIControl.id == 1, AIControl.revision == revision).update(
        {**changes, "revision": revision + 1}, synchronize_session=False)
    if affected != 1:
        db.rollback()
        raise HTTPException(409, "Настройки изменились. Обновите страницу.")
    target_id = target if len(target) <= 64 else hashlib.sha256(target.encode()).hexdigest()
    audit(db, actor, action, "ai_configuration", target_id, revision=revision + 1, reference=target)
    db.commit()
    db.expire_all()
    control.invalidate()
    return {"revision": revision + 1}


def provider_object(row, provider_id):
    stored = next((item for item in row.providers if item["config"]["id"] == provider_id), None)
    if stored:
        provider = AIProvider.model_validate(stored["config"])
        provider._managed, provider._sealed_key = True, stored.get("sealed_key")
    else:
        provider = providers.environment_registry().get(provider_id)
    return provider, stored


def update_provider(db, actor, provider_id, payload):
    row = row_for(db, payload.expected_revision)
    previous, stored = provider_object(row, provider_id)
    try:
        config = AIProvider(id=provider_id, **payload.model_dump(include={"base_url", "local", "enabled", "timeout_seconds", "project"}))
        destination(config.base_url, local=config.local)
    except (ValueError, OSError):
        raise HTTPException(422, "Проверьте адрес API: внешний — HTTPS/443 с публичным DNS; локальный — из списка разрешённых адресов сервера.") from None
    encrypted = stored.get("sealed_key") if stored else None
    if payload.key_action == "keep":
        if previous and previous.base_url != config.base_url and (encrypted or previous.api_key_env):
            raise HTTPException(422, "При изменении адреса API замените или удалите ключ. Старый ключ не передаётся на новый адрес.")
        config.api_key_env = previous.api_key_env if previous else ""
    elif payload.key_action == "replace":
        key = payload.api_key.get_secret_value().strip() if payload.api_key else ""
        if not key or len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise HTTPException(422, "Введите действующий API-ключ без пробелов и переносов строк.")
        try:
            encrypted = seal({"id": provider_id, "endpoint": str(config.base_url), "key": key})
        except Exception:
            raise HTTPException(503, "Хранилище ключей не настроено.") from None
    else:
        encrypted = None
    config._managed, config._sealed_key = True, encrypted
    if config.enabled and not providers.credential(config):
        raise HTTPException(422, "Для включения провайдера нужен действующий сохранённый ключ.")
    item = {"name": payload.name.strip(), "config": config.model_dump(mode="json"), "sealed_key": encrypted, "last_check": None}
    rows = [entry for entry in row.providers if entry["config"]["id"] != provider_id] + [item]
    if len(rows) > 50:
        raise HTTPException(422, "Можно настроить до 50 подключений.")
    return save(db, actor, row, "ai.provider.updated", provider_id, providers=rows)


def update_model(db, actor, payload):
    row = row_for(db, payload.expected_revision)
    provider, _ = provider_object(row, payload.provider)
    if not provider:
        raise HTTPException(422, "Сначала сохраните подключение провайдера.")
    if payload.approved and not provider.local and min(payload.input_rub_per_million, payload.output_rub_per_million) <= 0:
        raise HTTPException(422, "Для внешней модели укажите оба тарифа больше нуля.")
    if payload.approved and "critic_qa" in payload.roles and payload.model == settings.AI_MODEL_WRITER:
        raise HTTPException(422, "Для независимой проверки выберите модель, отличную от основной модели написания уроков.")
    item = payload.model_dump(mode="json", exclude={"expected_revision"})
    rows = [entry for entry in row.candidates if (entry["provider"], entry["model"]) != (payload.provider, payload.model)] + [item]
    if len(rows) > 200:
        raise HTTPException(422, "Можно настроить до 200 маршрутов моделей.")
    return save(db, actor, row, "ai.model.updated", payload.provider + "::" + payload.model, candidates=rows)


def check_provider(db, actor, provider_id, payload):
    row = row_for(db, payload.expected_revision)
    provider, stored = provider_object(row, provider_id)
    if provider is None:
        raise HTTPException(404, "Подключение не найдено.")
    key = providers.credential(provider)
    if not key:
        raise HTTPException(422, "Сначала сохраните API-ключ.")
    headers = {"Authorization": "Bearer " + key}
    if provider.project:
        headers["OpenAI-Project"] = provider.project
    probe = provider.model_copy(update={"timeout_seconds": min(provider.timeout_seconds, 10)})
    result = {"status": "unavailable", "checked_at": datetime.now(timezone.utc).isoformat(), "models": []}
    try:
        with http_client(probe) as client:
            response = client.get(str(provider.base_url).rstrip("/") + "/models", headers=headers)
        if response.status_code == 200:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError("Invalid models response")
            ids = [item["id"] for item in data["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)
                   and 0 < len(item["id"]) <= 255 and key not in item["id"] and all(ord(c) >= 32 for c in item["id"])]
            result.update(status="catalog_available", models=sorted(set(ids))[:500])
        elif response.status_code in {401, 403}:
            result["status"] = "access_denied"
        elif response.status_code in {404, 405}:
            result["status"] = "catalog_unsupported"
        elif response.status_code == 429:
            result["status"] = "rate_limited"
    except Exception:
        pass  # Never persist or echo remote bodies, headers or exception text.
    db.expire_all()
    row = row_for(db, payload.expected_revision)
    item = {**stored, "last_check": result} if stored else {
        "name": provider.id, "config": provider.model_dump(mode="json"), "sealed_key": None, "last_check": result}
    rows = [entry for entry in row.providers if entry["config"]["id"] != provider_id] + [item]
    return {**save(db, actor, row, "ai.provider.checked", provider_id, providers=rows), **result}


def overview(db):
    row = row_for(db)
    ids = set(providers.environment_registry()) | {item["config"]["id"] for item in row.providers}
    items = []
    for provider_id in sorted(ids):
        provider, stored = provider_object(row, provider_id)
        items.append({**provider.model_dump(mode="json", exclude={"api_key_env"}),
            "name": stored["name"] if stored else provider_id,
            "credential_source": "encrypted" if provider._sealed_key else "environment" if provider.api_key_env else "none",
            "has_key": bool(providers.credential(provider)), "available": providers.available(provider),
            "managed": bool(stored), "last_check": stored.get("last_check") if stored else None})
    models = {(item.provider, item.model): item.model_copy(deep=True) for item in settings.AI_ROUTING_CANDIDATES}
    explicit = set(models)
    for role in ROLES:
        for model in {model_policy(role).model, model_policy(role).fallback}:
            key = ("vsellm", model)
            if key not in models:
                item = candidate_price("vsellm", model, role)
                if item:
                    models[key] = item.model_copy(deep=True)
            elif key not in explicit and role not in models[key].roles:
                models[key].roles.append(role)
            if key in models and key not in explicit:
                models[key].vision |= role == "vision"
                models[key].independent_review |= role == "critic_qa"
    models.update({(item["provider"], item["model"]): AIRoutingCandidate.model_validate(item) for item in row.candidates})
    stats = {}
    calls = db.query(AICall).join(GenerationRun, GenerationRun.id == AICall.run_id).filter(
        GenerationRun.created_at >= datetime.utcnow() - timedelta(days=1)).order_by(AICall.id.desc()).limit(5001).all()
    for call in calls[:5000]:
        route = call.routing or {}
        entry = stats.setdefault(route.get("selected_provider", "unknown"), {"calls": 0, "errors": 0, "cached_tokens": 0, "input_tokens": 0, "output_tokens": 0, "estimated_rub": None})
        entry["calls"] += 1
        entry["errors"] += int(bool(call.error_code))
        entry["cached_tokens"] += call.cached_tokens
        entry["input_tokens"] += call.input_tokens
        entry["output_tokens"] += call.output_tokens
        if route.get("usage_priced_uncached_rub") is not None:
            entry["estimated_rub"] = str(Decimal(entry["estimated_rub"] or "0") + Decimal(route["usage_priced_uncached_rub"]))
    return {"revision": row.revision, "providers": items, "models": [item.model_dump(mode="json") for item in models.values()],
            "policy": {**control.policy(), **row.policy,
                       "local_only": settings.AI_LOCAL_ONLY or row.policy.get("local_only", control.policy()["local_only"])},
            "catalog": CATALOG, "roles": ROLES,
            "stats": stats, "stats_truncated": len(calls) > 5000, "local_endpoints": settings.AI_LOCAL_ENDPOINTS,
            "cache_ttl_seconds": settings.AI_CACHE_TTL_SECONDS, "reload_seconds": 5}
