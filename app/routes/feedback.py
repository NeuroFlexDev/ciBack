from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.user import User
from app.repositories.feedback import FeedbackRepository
from app.schemas.feedback import FeedbackCreate, FeedbackResponse
from app.services.access_control import ensure_can_manage_lesson
from app.services.auth import get_current_user_dep

router = APIRouter(prefix="/feedback", tags=["Feedback"])


@router.post("/", response_model=FeedbackResponse, summary="Leave feedback for a lesson")
def leave_feedback(
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    try:
        return FeedbackRepository.create_feedback(db, payload, author_id=user.id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/lesson/{lesson_id}", response_model=list[FeedbackResponse], summary="List feedback for a lesson")
def get_feedback_for_lesson(
    lesson_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id, Lesson.is_deleted == False).first()
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    ensure_can_manage_lesson(user, lesson)
    try:
        return FeedbackRepository.list_for_lesson(db, lesson_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
