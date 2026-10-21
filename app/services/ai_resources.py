"""Database-serialized admission and cost caps shared by every application worker.

Interactive requests use a separate, bounded pool: long generation/OCR jobs can
never consume its slots. There is no automatic paid retry in this boundary.
Timeouts retain worst-case spend; only proven unsent requests release it.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_UP
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func, select, update
from app.core.config import settings
from app.models.ai_resources import AIResourceMutex, AIResourceUsage
from app.models.course import Course
from app.models.user import User

ACTIVE = {"queued", "running"}


class CapacityBusy(HTTPException):
    request_sent = False
    def __init__(self, code="ai_capacity", message="Все доступные слоты заняты. Повторите позже; данные сохранены."):
        super().__init__(429, {"code": code, "message": message}, headers={"Retry-After": "15"})


def locked(db):
    """An UPDATE obtains an actual writer lock on SQLite as well as PostgreSQL."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Unsupported database for atomic AI admission")
    db.execute(insert(AIResourceMutex).values(id=1, revision=0).on_conflict_do_nothing(index_elements=["id"]))
    db.execute(update(AIResourceMutex).where(AIResourceMutex.id == 1).values(revision=AIResourceMutex.revision + 1))
    # A dead process can retain a reservation, never spend it again. The expiry
    # exceeds the transport/job deadline; uncertain cost remains consumed.
    # Cancellation/enqueue failures may happen in another process. Reconcile
    # terminal backing records without waiting for a maintenance job.
    from app.models.generation_run import GenerationRun
    from app.models.materials import CourseImage
    for row in db.query(AIResourceUsage).filter(AIResourceUsage.status == "running", AIResourceUsage.expires_at < datetime.utcnow()):
        row.status, row.finished_at = "unknown", datetime.utcnow()
        if row.lane == "background" and row.run_id:
            run = db.get(GenerationRun, row.run_id)
            if run and run.status in {"queued", "running"}:
                run.status, run.cancel_requested = "paused", True
                run.error_code = "worker_result_unknown"
                run.error_message = "Рабочий процесс не подтвердил завершение. Этапы сохранены; автоматический платный повтор остановлен."
                run.finished_at = datetime.utcnow()
                course = db.get(Course, run.course_id)
                if course:
                    course.status = "ready" if course.current_graph_id else "configured"
        elif row.operation == "image":
            image = db.get(CourseImage, int(row.key.split(":")[-1]))
            if image and image.status == "generating":
                image.status, image.error_code, image.size_bytes = "failed", "image_result_unknown", 0
    for row in db.query(AIResourceUsage).filter(AIResourceUsage.lane == "background", AIResourceUsage.status == "queued"):
        run = db.get(GenerationRun, row.run_id) if row.run_id else None
        terminal = bool(run and (run.status not in {"queued", "running"} or run.cancel_requested))
        if row.operation == "image":
            image = db.get(CourseImage, int(row.key.split(":")[-1]))
            terminal = image is None or image.status not in {"queued", "generating"}
        if terminal:
            row.status, row.finished_at = "cancelled", datetime.utcnow()
            row.tokens = row.ocr_pages = 0
            row.cost_rub = Decimal(0)
    db.flush()


def scope(db, owner_id, course_id=None):
    course = db.get(Course, course_id) if course_id else None
    user = db.get(User, owner_id)
    return course.organization_id if course else user.organization_id


def scoped(query, organization_id, owner_id):
    if organization_id is not None:
        return query.filter(AIResourceUsage.organization_id == organization_id)
    return query.filter(AIResourceUsage.organization_id.is_(None), AIResourceUsage.owner_id == owner_id)


def _capacity(db, owner_id, organization_id, lane, state, *, exclude=None):
    query = db.query(AIResourceUsage).filter(AIResourceUsage.lane == lane, AIResourceUsage.status == state)
    if exclude:
        query = query.filter(AIResourceUsage.id != exclude)
    category = "INTERACTIVE" if lane == "interactive" else ("QUEUED" if state == "queued" else "BACKGROUND")
    if query.count() >= getattr(settings, f"AI_INSTALL_{category}_LIMIT") or scoped(query, organization_id, owner_id).count() >= getattr(settings, f"AI_ORG_{category}_LIMIT"):
        raise CapacityBusy()


def _budget(db, owner_id, organization_id, tokens, pages, cost, run_id=None):
    month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    query = db.query(AIResourceUsage).filter(AIResourceUsage.created_at >= month)
    for suffix, subset in (("INSTALL", query), ("ORG", scoped(query, organization_id, owner_id))):
        spent = subset.with_entities(func.coalesce(func.sum(AIResourceUsage.tokens), 0),
            func.coalesce(func.sum(AIResourceUsage.ocr_pages), 0), func.coalesce(func.sum(AIResourceUsage.cost_rub), 0)).one()
        if (spent[0] + tokens > getattr(settings, f"AI_{suffix}_MONTHLY_TOKENS") or
            spent[1] + pages > getattr(settings, f"AI_{suffix}_MONTHLY_OCR_PAGES") or
            spent[2] + cost > getattr(settings, f"AI_{suffix}_MONTHLY_RUB")):
            raise CapacityBusy("ai_resource_budget", "Исчерпан месячный бюджет AI или обработки документов. Сохранённые материалы доступны.")
    if run_id and cost:
        used = db.query(func.coalesce(func.sum(AIResourceUsage.cost_rub), 0)).filter(AIResourceUsage.run_id == run_id).scalar()
        if used + cost > settings.AI_MAX_RUB_PER_RUN:
            raise CapacityBusy("ai_run_cost_budget", "Достигнут денежный лимит запуска. Завершённые этапы сохранены.")


def reserve(db, *, owner_id, course_id=None, operation, lane="metered", tokens=0, pages=0,
            cost=Decimal(0), key=None, run_id=None, queued=False, timeout=None):
    locked(db)
    key = key or f"{operation}:{uuid4().hex}"
    existing = db.query(AIResourceUsage).filter_by(key=key).first()
    if existing:
        if queued and existing.status not in ACTIVE and run_id:
            from app.models.generation_run import GenerationRun
            run = db.get(GenerationRun, run_id)
            if run.status == "queued" and not run.cancel_requested:
                _capacity(db, owner_id, existing.organization_id, lane, "queued")
                existing.status, existing.finished_at, existing.expires_at = "queued", None, None
        return existing
    org_id = scope(db, owner_id, course_id)
    status = "queued" if queued else "running"
    if lane in {"background", "interactive"}:
        _capacity(db, owner_id, org_id, lane, status)
    cost = Decimal(cost).quantize(Decimal(".000001"), rounding=ROUND_UP)
    _budget(db, owner_id, org_id, tokens, pages, cost, run_id)
    row = AIResourceUsage(key=key, owner_id=owner_id, organization_id=org_id, course_id=course_id,
        operation=operation, lane=lane, status=status, tokens=tokens, ocr_pages=pages, cost_rub=cost,
        run_id=run_id, expires_at=None if queued else datetime.utcnow() + timedelta(seconds=(timeout or 300) + 120))
    db.add(row); db.flush()
    return row


def claim(db, key):
    locked(db)
    row = db.query(AIResourceUsage).filter_by(key=key).first()
    if row is None:
        raise RuntimeError("Missing durable job admission")
    if row.status != "queued":
        db.commit()
        return False
    _capacity(db, row.owner_id, row.organization_id, row.lane, "running", exclude=row.id)
    row.status = "running"
    row.expires_at = datetime.utcnow() + timedelta(seconds=settings.AI_JOB_TIMEOUT_SECONDS + 120)
    db.commit()
    return True


def finish(db, row, *, usage=None, unsent=False, failed=False, pages=None):
    if isinstance(row, str):
        row = db.query(AIResourceUsage).filter_by(key=row).first()
    if row is None:
        return
    row.status = "unavailable" if unsent else ("unknown" if failed else "completed")
    row.finished_at = datetime.utcnow()
    if unsent:
        row.tokens = row.ocr_pages = 0
        row.cost_rub = Decimal(0)
    elif usage is not None:
        # Monetary reservations intentionally remain conservative: no cached
        # token discount or provider rounding can create an understated invoice.
        row.tokens = max(0, int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)))
    if pages is not None:
        row.ocr_pages = max(0, pages)
    db.commit()


def text_price(role, input_tokens, output_tokens, *, model=None, provider=None):
    from app.ai.models import model_policy
    from app.ai.pricing import candidate_price
    from app.ai.routing import enabled_candidates
    from app.ai.providers import registry
    policy = model_policy(role)
    pairs = {(provider or "vsellm", model or policy.model)}
    if provider is None:
        pairs.update((item.provider, item.model) for item in enabled_candidates(role))
        pairs.add(("vsellm", policy.fallback))
    costs = []
    for provider_id, model_id in pairs:
        configured = registry().get(provider_id)
        if configured and configured.local:
            costs.append(Decimal(0))
            continue
        candidate = candidate_price(provider_id, model_id, role)
        if candidate is None:
            error = HTTPException(503, {"code": "ai_price_unconfigured", "message": "Не настроена цена разрешённого AI-маршрута"})
            error.request_sent = False
            raise error
        costs.append((input_tokens * candidate.input_rub_per_million + output_tokens * candidate.output_rub_per_million) / Decimal(1000000))
    return max(costs)


def completion(db, owner_id, course_id, role, messages, *, invoke=None, **kwargs):
    from app.ai import gateway
    from app.ai.models import model_policy
    output = kwargs.get("max_tokens") or model_policy(role).max_output_tokens
    tokens = gateway.token_count(messages)
    row = reserve(db, owner_id=owner_id, course_id=course_id, operation=role, lane="interactive",
        tokens=tokens + output, cost=text_price(role, tokens, output, model=kwargs.get("model"), provider=kwargs.get("provider")))
    db.commit()
    try:
        result = (invoke or gateway.invoke_messages)(role, messages, **kwargs)
        finish(db, row, usage=result.get("usage") if result.get("usage_reported", bool(result.get("usage"))) else None)
        return result
    except Exception as exc:
        db.rollback()
        finish(db, row, usage=getattr(exc, "ai_usage", None), unsent=getattr(exc, "request_sent", None) is False, failed=True)
        raise


def usage_summary(db, owner_id):
    org_id = scope(db, owner_id)
    month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    query = scoped(db.query(AIResourceUsage).filter(AIResourceUsage.created_at >= month), org_id, owner_id)
    values = query.with_entities(func.coalesce(func.sum(AIResourceUsage.tokens), 0), func.coalesce(func.sum(AIResourceUsage.ocr_pages), 0), func.coalesce(func.sum(AIResourceUsage.cost_rub), 0)).one()
    return {"month": month.date().isoformat(), "tokens_reserved_or_used": values[0], "ocr_pages_reserved_or_used": values[1],
        "cost_rub_reserved_upper_bound": str(values[2]), "tokens_limit": settings.AI_ORG_MONTHLY_TOKENS,
        "ocr_pages_limit": settings.AI_ORG_MONTHLY_OCR_PAGES, "cost_rub_limit": str(settings.AI_ORG_MONTHLY_RUB),
        "background_running_limit": settings.AI_ORG_BACKGROUND_LIMIT, "queued_limit": settings.AI_ORG_QUEUED_LIMIT,
        "interactive_running_limit": settings.AI_ORG_INTERACTIVE_LIMIT}
