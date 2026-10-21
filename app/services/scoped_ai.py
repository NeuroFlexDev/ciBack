"""Cost reservation for interactive AI outside a course generation run."""
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from app.ai import gateway
from app.ai.models import model_policy
from app.models.materials import ScopedAIUsage, LearnerAssistantMessage
from app.models.user import User
from app.services.material_quota import material_policy


def tracked_completion(db, owner_id, course_id, role, messages, *, invoke=None, **kwargs):
    user = db.execute(select(User).where(User.id == owner_id).with_for_update()).scalar_one()
    from app.services.ai_access_boundary import principal_stamp, author_result_access
    stamp = principal_stamp(user)
    if role in {"chat", "canvas"}:
        from app.services.organization_ai_context import organization_ai_context, with_organization_context
        messages = with_organization_context(messages, organization_ai_context(db, user.organization_id))
    ids = [owner_id]
    if user.organization_id:
        from app.services.organization_access import lock_org, member_ids
        org = lock_org(db, user.organization_id)
        if not org.is_active:
            raise HTTPException(403, "Организация приостановлена")
        ids = member_ids(db, org.id)
    start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = db.query(ScopedAIUsage).filter(ScopedAIUsage.owner_id.in_(ids), ScopedAIUsage.created_at >= start).count()
    used += db.query(LearnerAssistantMessage).filter(LearnerAssistantMessage.owner_id.in_(ids), LearnerAssistantMessage.created_at >= start).count()
    if used >= material_policy(db, user)["chat_monthly_limit"]:
        raise HTTPException(429, "Достигнут месячный лимит интерактивных запросов AI")
    record = ScopedAIUsage(owner_id=owner_id, course_id=course_id, operation=role, status="reserved",
        input_tokens=gateway.token_count(messages), output_tokens=kwargs.get("max_tokens") or model_policy(role).max_output_tokens)
    db.add(record); db.commit()
    try:
        # Interactive operations use one bounded transport attempt. The user can
        # explicitly retry; a timeout never silently creates extra paid requests.
        from app.services.ai_resources import completion
        result = completion(db, owner_id, course_id, role, messages, invoke=invoke, **kwargs)
        usage = result.get("usage") or {}
        record.input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", record.input_tokens))
        record.output_tokens = usage.get("output_tokens", usage.get("completion_tokens", record.output_tokens))
        record.model, record.provider = result.get("model"), result.get("provider")
        record.status = "completed" if usage else "usage_unreported"
        db.commit()
    except Exception as exc:
        record.status = "unavailable" if getattr(exc, "request_sent", None) is False else "usage_unknown"
        record.error_code = gateway.safe_error(exc)
        if record.status == "unavailable":
            record.input_tokens = record.output_tokens = 0
        db.commit()
        raise
    try:
        author_result_access(db, owner_id, stamp, course_id, role)
    except HTTPException:
        record.error_code = "ai_access_revoked"
        db.commit()
        raise
    return result
