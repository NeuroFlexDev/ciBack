"""Monthly request counts use the tenant recorded when a request was admitted."""
from datetime import datetime
from fastapi import HTTPException
from app.models.materials import ScopedAIUsage, LearnerAssistantMessage


def monthly_requests(db, owner_id, organization_id, *, month=None):
    month = month or datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    confirmed = legacy = 0
    for model in (ScopedAIUsage, LearnerAssistantMessage):
        query = db.query(model).filter(model.created_at >= month)
        scoped = query.filter(model.scope_status.in_(("captured", "resolved")), model.organization_id == organization_id)
        if organization_id is None:
            scoped = scoped.filter(model.owner_id == owner_id)
        confirmed += scoped.count()
        # A legacy receipt without a reliable course/assignment cannot become
        # another tenant's history. Keep only this actor's uncertainty visible;
        # conservatively include it in their admission until the month closes.
        legacy += query.filter(model.scope_status == "legacy_unknown", model.owner_id == owner_id).count()
    return {"confirmed": confirmed, "legacy_unattributed": legacy, "admission_total": confirmed + legacy}


def admit_request(db, user):
    """Caller holds org→user locks and must insert the receipt before commit.

    The resource mutex also serializes SQLite, where SELECT FOR UPDATE alone is
    not a writer lock. Every counted request uses this same admission boundary.
    """
    from app.services.ai_resources import locked
    from app.services.material_quota import material_policy
    from app.models.administration import Organization
    organization = db.get(Organization, user.organization_id) if user.organization_id else None
    if user.organization_id and (not organization or not organization.is_active):
        raise HTTPException(403, "Организация приостановлена")
    locked(db)
    usage = monthly_requests(db, user.id, user.organization_id)
    if not (organization and organization.ai_unlimited) and usage["admission_total"] >= material_policy(db, user)["chat_monthly_limit"]:
        raise HTTPException(429, {"code": "assistant_limit",
            "message": "Достигнут месячный лимит интерактивных запросов AI. Уроки и отправка ответов доступны.",
            "confirmed_requests": usage["confirmed"],
            "own_legacy_unattributed_requests": usage["legacy_unattributed"]})
    return usage
