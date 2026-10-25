from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.core.config import settings
from app.models.materials import LearnerAssistantMessage
from app.services.auth_service import get_current_user
from app.services import learner_assistant_service as assistant
from app.schemas.mobile_contract import AssistantHistoryResponse, AssistantMessageResponse

router = APIRouter(prefix="/learning/assignments/{assignment_id}/assistant")


class Question(BaseModel):
    lesson_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/messages", response_model=AssistantHistoryResponse)
def history(assignment_id: int, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    assignment, _ = assistant.assignment_context(db, user, assignment_id, new_request=False)
    assistant.expire_history(db, user.id)
    query = db.query(LearnerAssistantMessage).filter_by(owner_id=user.id, assignment_id=assignment_id,
        course_version_id=assignment.course_version_id).filter(LearnerAssistantMessage.status != "deleted")
    total = query.count()
    items = query.order_by(LearnerAssistantMessage.id.desc()).offset(offset).limit(limit).all()
    db.commit()
    from app.services.learning_service import settings_for
    config = settings_for(db, assignment.organization_id)
    can_send = config.features.get("assistant") is not False and config.rules.get("allow_ai_assistant") is not False and assignment.rules.get("allow_ai_assistant", True)
    return {"items": [assistant.message_out(item) for item in items], "total": total,
            "limit": limit, "offset": offset, "retention_days": settings.AI_CHAT_RETENTION_DAYS,
            "can_send": bool(can_send), "disabled_reason": None if can_send else "Помощник отключён правилами организации или назначения"}


@router.post("/messages", response_model=AssistantMessageResponse)
def ask(assignment_id: int, payload: Question, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return assistant.message_out(assistant.send_message(db, user, assignment_id,
        payload.lesson_id, payload.question.strip(), payload.request_id))


@router.delete("/messages", status_code=204)
def clear_history(assignment_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    assistant.assignment_context(db, user, assignment_id, new_request=False)
    # Keep only non-content usage/idempotency records: deletion must not reset
    # a monthly paid quota or send a previously handled request twice.
    db.query(LearnerAssistantMessage).filter_by(owner_id=user.id, assignment_id=assignment_id).update(
        {"question": "", "answer": None, "citations": [], "status": "deleted"}, synchronize_session=False)
    db.commit()
