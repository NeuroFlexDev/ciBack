"""Database-serialized admission and cost caps shared by every application worker.

Interactive requests use a separate, bounded pool: long generation/OCR jobs can
never consume its slots. There is no automatic paid retry in this boundary.
Timeouts retain worst-case spend; only proven unsent requests release it.
"""
from app.ai import control as ai_control
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_UP
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import func, select, update, or_
from app.core.config import settings
from app.models.ai_resources import AIResourceMutex, AIResourceUsage
from app.models.course import Course
from app.models.user import User

ACTIVE = {"queued", "running"}
RESOURCE_SETTINGS = {"queued_limit": "AI_ORG_QUEUED_LIMIT", "background_limit": "AI_ORG_BACKGROUND_LIMIT",
    "interactive_limit": "AI_ORG_INTERACTIVE_LIMIT", "monthly_tokens": "AI_ORG_MONTHLY_TOKENS",
    "monthly_ocr_pages": "AI_ORG_MONTHLY_OCR_PAGES", "monthly_rub": "AI_ORG_MONTHLY_RUB"}


def organization_limits(db, organization_id):
    from app.models.materials import OrganizationMaterialPolicy
    row = db.get(OrganizationMaterialPolicy, organization_id) if organization_id else None
    overrides = (row.resource_limits or {}) if row else {}
    return {key: (Decimal(overrides[key]) if key == "monthly_rub" else int(overrides[key]))
        if key in overrides else getattr(settings, setting) for key, setting in RESOURCE_SETTINGS.items()}


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
        # Do not lock domain rows while holding the resource mutex: authoring
        # acquires those locks first. Polling reconciles expired jobs separately.
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


def background_admission_paused():
    if settings.ENV == "test":
        return False
    from redis import Redis, RedisError
    try:
        with Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2) as connection:
            return bool(connection.exists("rq:suspended"))
    except RedisError:
        raise CapacityBusy("ai_queue_unavailable", "Очередь временно недоступна. Материалы сохранены; повторите позже.") from None


def _capacity(db, owner_id, organization_id, lane, state, *, exclude=None):
    # Deployment suspends admission under this same database mutex. Existing
    # jobs can finish their internal AI calls; queued work waits for resume.
    if lane == "background" and state == "running" and background_admission_paused():
        raise CapacityBusy("ai_deployment_pending", "Сервис обновляется. Задания в очереди сохранены; обработка продолжится после обновления.")
    query = db.query(AIResourceUsage).filter(AIResourceUsage.lane == lane, AIResourceUsage.status == state)
    if exclude:
        query = query.filter(AIResourceUsage.id != exclude)
    category = "INTERACTIVE" if lane == "interactive" else ("QUEUED" if state == "queued" else "BACKGROUND")
    org_limit = organization_limits(db, organization_id)[category.lower() + "_limit"]
    if query.count() >= getattr(settings, f"AI_INSTALL_{category}_LIMIT") or scoped(query, organization_id, owner_id).count() >= org_limit:
        raise CapacityBusy()


def _budget(db, owner_id, organization_id, tokens, pages, cost, run_id=None):
    from app.services.ai_entitlements import unlimited_ai
    from app.models.administration import Organization
    if unlimited_ai(db, organization_id):
        return
    month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    query = db.query(AIResourceUsage).filter(AIResourceUsage.created_at >= month)
    # Exempt traffic is metered separately and must not exhaust other tenants' pool.
    exempt = select(Organization.id).where(Organization.ai_unlimited.is_(True))
    installation = query.filter((AIResourceUsage.organization_id.is_(None)) | (~AIResourceUsage.organization_id.in_(exempt)))
    limits = organization_limits(db, organization_id)
    for suffix, subset in (("INSTALL", installation), ("ORG", scoped(query, organization_id, owner_id))):
        spent = subset.with_entities(func.coalesce(func.sum(AIResourceUsage.tokens), 0),
            func.coalesce(func.sum(AIResourceUsage.ocr_pages), 0), func.coalesce(func.sum(AIResourceUsage.cost_rub), 0)).one()
        limit = lambda name: limits[name.lower()] if suffix == "ORG" else getattr(settings, f"AI_INSTALL_{name}")
        if (spent[0] + tokens > limit("MONTHLY_TOKENS") or spent[1] + pages > limit("MONTHLY_OCR_PAGES") or spent[2] + cost > limit("MONTHLY_RUB")):
            raise CapacityBusy("ai_resource_budget", "Исчерпан месячный бюджет AI или обработки документов. Сохранённые материалы доступны.")
    if run_id and cost:
        used = db.query(func.coalesce(func.sum(AIResourceUsage.cost_rub), 0)).filter(AIResourceUsage.run_id == run_id).scalar()
        if used + cost > settings.AI_MAX_RUB_PER_RUN:
            raise CapacityBusy("ai_run_cost_budget", "Достигнут денежный лимит запуска. Завершённые этапы сохранены.")


def reserve(db, *, owner_id, course_id=None, operation, lane="metered", tokens=0, pages=0,
            cost=Decimal(0), key=None, run_id=None, queued=False, timeout=None, partial_ocr=False):
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
    from app.services.ai_entitlements import unlimited_ai
    if partial_ocr and not unlimited_ai(db, org_id):
        # Text-only PDFs need no OCR. Atomically reserve the remaining allowance
        # and let the isolated parser enforce it before each OCR dispatch.
        month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = db.query(AIResourceUsage).filter(AIResourceUsage.created_at >= month)
        from app.models.administration import Organization
        exempt = select(Organization.id).where(Organization.ai_unlimited.is_(True))
        install_used = query.filter(or_(AIResourceUsage.organization_id.is_(None), ~AIResourceUsage.organization_id.in_(exempt))).with_entities(func.coalesce(func.sum(AIResourceUsage.ocr_pages), 0)).scalar()
        org_used = scoped(query, org_id, owner_id).with_entities(func.coalesce(func.sum(AIResourceUsage.ocr_pages), 0)).scalar()
        pages = max(0, min(pages, settings.AI_INSTALL_MONTHLY_OCR_PAGES - install_used,
            organization_limits(db, org_id)["monthly_ocr_pages"] - org_used))
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


def prepare_generation_retry(db, run):
    """Re-arm admission in the same course-locked transaction as an explicit retry.

    This is only a concurrency slot. Paid call receipts and their conservative
    reservations remain unchanged. A previous worker must release its live slot
    before a retry can reuse it.
    """
    locked(db)
    row = db.query(AIResourceUsage).filter_by(key=f"generation:{run.id}").first()
    if row and row.status == "running":
        raise HTTPException(409, {"code": "generation_worker_finishing",
            "message": "Предыдущий рабочий процесс ещё завершает запуск. Повторите через несколько секунд."})
    if row:
        _capacity(db, row.owner_id, row.organization_id, row.lane, "queued", exclude=row.id)
        row.status, row.finished_at, row.expires_at = "queued", None, None
        return row
    return reserve(db, owner_id=run.owner_id, course_id=run.course_id, operation="generation",
        lane="background", key=f"generation:{run.id}", run_id=run.id, queued=True)


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
    if ai_control.policy()["local_only"]:
        pairs = {(name, selected) for name, selected in pairs if registry().get(name) and registry()[name].local}
        if not pairs:
            from app.ai.providers import AIUnavailable
            raise AIUnavailable()
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


def completion(db, owner_id, course_id, role, messages, *, invoke=None, expected_principal=None, **kwargs):
    from app.ai import gateway
    from app.ai.models import model_policy
    if expected_principal is not None:
        # Receipt insertion commits before provider admission. A membership
        # change in this gap must not charge a different tenant or send the old
        # tenant's context with the new identity. Locks stay org→user→resource.
        from app.services.organization_access import lock_member
        from app.models.administration import Organization
        try:
            user = lock_member(db, owner_id, expected_auth_version=expected_principal[1])
            organization = db.get(Organization, user.organization_id) if user.organization_id else None
            if user.organization_id != expected_principal[0] or (organization and not organization.is_active):
                raise HTTPException(403, {"code": "ai_access_revoked", "message": "Доступ изменился до отправки AI-запроса."})
        except HTTPException as error:
            error.request_sent = False
            raise
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
    limits = organization_limits(db, org_id)
    from app.models.materials import CourseImage
    from app.services.material_quota import material_policy, used_disk_bytes
    material = material_policy(db, db.get(User, owner_id))
    active = scoped(db.query(AIResourceUsage), org_id, owner_id)
    courses = db.query(Course.id).filter(Course.organization_id == org_id)
    if org_id is None:
        courses = courses.filter(Course.owner_id == owner_id)
    images = db.query(CourseImage).filter(CourseImage.course_id.in_(courses), CourseImage.prompt.is_not(None), CourseImage.created_at >= month).count()
    from app.services.interactive_quota import monthly_requests
    requests = monthly_requests(db, owner_id, org_id, month=month)
    from app.services.ai_entitlements import unlimited_ai
    return {"ai_unlimited": unlimited_ai(db, org_id), "month": month.date().isoformat(), "tokens_reserved_or_used": values[0], "ocr_pages_reserved_or_used": values[1],
        "cost_rub_reserved_upper_bound": str(values[2]), "tokens_limit": limits["monthly_tokens"],
        "ocr_pages_limit": limits["monthly_ocr_pages"], "cost_rub_limit": str(limits["monthly_rub"]),
        "background_running_limit": limits["background_limit"], "queued_limit": limits["queued_limit"],
        "interactive_running_limit": limits["interactive_limit"], "resource_limits": {key: str(value) if isinstance(value, Decimal) else value for key, value in limits.items()},
        "used_disk_bytes": used_disk_bytes(db, [owner_id], organization_id=org_id), "disk_bytes_limit": material["disk_bytes_limit"],
        "images_this_month": images, "image_monthly_limit": material["image_monthly_limit"],
        "interactive_requests_this_month": requests["confirmed"],
        "interactive_requests_own_legacy_unattributed": requests["legacy_unattributed"],
        "interactive_requests_admission_total": requests["admission_total"],
        "chat_monthly_limit": material["chat_monthly_limit"],
        "background_running": active.filter_by(lane="background", status="running").count(),
        "interactive_running": active.filter_by(lane="interactive", status="running").count(),
        "queued": active.filter_by(lane="background", status="queued").count()}


def reconcile_expired_run(db, run):
    row = db.query(AIResourceUsage).filter_by(key=f"generation:{run.id}").first()
    if row and row.expires_at and row.expires_at < datetime.utcnow() and run.status in {"running", "queued"}:
        course = db.execute(select(Course).where(Course.id == run.course_id).with_for_update()).scalar_one_or_none()
        db.refresh(run)
        # A retry or worker claim may have committed while polling waited for
        # the course lock. Re-read the lease under its row lock: never cancel a
        # new attempt using the expired lease cached by this status request.
        row = db.execute(select(AIResourceUsage).where(AIResourceUsage.key == f"generation:{run.id}")
            .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if (run.status not in {"running", "queued"} or row is None
                or row.status not in {"running", "unknown"} or not row.expires_at
                or row.expires_at >= datetime.utcnow()
                or (run.queued_at and row.expires_at <= run.queued_at)):
            return
        run.status, run.cancel_requested, run.retryable = "paused", True, True
        run.error_code = "worker_result_unknown"
        run.error_message = "Рабочий процесс не подтвердил завершение. Автоматический платный повтор остановлен; этапы сохранены."
        run.finished_at = datetime.utcnow()
        if course:
            course.status = "ready" if course.current_graph_id else "configured"
        db.commit()
