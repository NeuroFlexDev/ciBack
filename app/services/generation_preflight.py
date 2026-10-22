"""Reject an impossible run before spending tokens or a monthly launch slot."""
import math
from fastapi import HTTPException
from app.core.config import settings
from app.ai.evidence import source_batches
from app.services.source_catalog_service import build_source_catalog


def generation_preflight(documents, configuration):
    source_chars = sum(len(chunk.text) for document in documents for chunk in document.chunks if not chunk.is_deleted)
    catalog = build_source_catalog(documents, max_chars=settings.AI_MAX_SOURCE_CHARS)
    from app.services.web_search_service import preflight_search
    web = preflight_search(configuration, catalog)
    batches = len(source_batches(catalog, settings.AI_INGESTION_BATCH_CHARS)) if catalog else 0
    count = configuration["lesson_count"]
    # Writer per lesson; assessment and independent QA start with pairs.
    minimum_calls = batches + 2 + count + 2 * math.ceil(count / 2)
    reserved_output = batches * 6000 + 22000 + count * 6000 + math.ceil(count / 2) * 18000
    reasons = []
    if not catalog:
        reasons.append("Нет извлечённого текста для генерации")
    if source_chars > settings.AI_MAX_SOURCE_CHARS:
        reasons.append("Объём источников превышает лимит курса")
    if minimum_calls > settings.AI_MAX_CALLS_PER_RUN:
        reasons.append("Количество уроков требует больше вызовов AI, чем допускает бюджет запуска")
    if reserved_output > settings.AI_MAX_TOKENS_PER_RUN:
        reasons.append("Бюджета токенов недостаточно для заданного числа уроков")
    return {"allowed": not reasons, "reasons": reasons, "source_characters": source_chars, "web_search": web,
            "source_bytes": sum(item.size_bytes for item in documents), "source_batches": batches,
            "lesson_count": count, "minimum_calls": minimum_calls,
            "calls_limit": settings.AI_MAX_CALLS_PER_RUN, "output_token_reservation": reserved_output,
            "tokens_limit": settings.AI_MAX_TOKENS_PER_RUN,
            "cost_rub_hard_limit": str(settings.AI_MAX_RUB_PER_RUN),
            "cost_scope": "Text generation calls including validation repairs; separate embedding, OCR and image reservations use organization/installation limits.",
            "organization_monthly_cost_limit_rub": str(settings.AI_ORG_MONTHLY_RUB),
            "source_characters_limit": settings.AI_MAX_SOURCE_CHARS,
            "timeout_seconds": settings.AI_JOB_TIMEOUT_SECONDS,
            "note": "Расчёт нижней границы. Входной контекст, повторы и исправления используют оставшийся бюджет; расход каждого вызова проверяется сервером."}


def require_preflight(documents, configuration):
    result = generation_preflight(documents, configuration)
    if not result["allowed"]:
        raise HTTPException(422, {"code": "generation_preflight_failed", "message": "Параметры генерации превышают доступный бюджет", "preflight": result})
    return result
