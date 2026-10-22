"""Cost reservation for interactive AI outside a course generation run."""
from fastapi import HTTPException
from app.ai import gateway
from app.ai.models import model_policy
from app.models.materials import ScopedAIUsage
from app.models.user import User
from app.services.organization_access import lock_member
from app.services.interactive_quota import admit_request


def tracked_completion(db, owner_id, course_id, role, messages, *, invoke=None, **kwargs):
    from app.services.ai_access_boundary import principal_stamp, author_result_access
    stamp = principal_stamp(db.get(User, owner_id))
    user = lock_member(db, owner_id, expected_auth_version=stamp[1])
    if principal_stamp(user) != stamp:
        raise HTTPException(403, "Доступ изменился до начала запроса")
    if role in {"chat", "canvas"}:
        from app.services.organization_ai_context import organization_ai_context, with_organization_context
        messages = with_organization_context(messages, organization_ai_context(db, user.organization_id))
    admit_request(db, user)
    record = ScopedAIUsage(owner_id=owner_id, organization_id=user.organization_id, scope_status="captured",
        course_id=course_id, operation=role, status="reserved",
        input_tokens=gateway.token_count(messages), output_tokens=kwargs.get("max_tokens") or model_policy(role).max_output_tokens)
    db.add(record); db.commit()
    try:
        # Interactive operations use one bounded transport attempt. The user can
        # explicitly retry; a timeout never silently creates extra paid requests.
        from app.services.ai_resources import completion
        result = completion(db, owner_id, course_id, role, messages, invoke=invoke, expected_principal=stamp, **kwargs)
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
