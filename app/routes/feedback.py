# app/routes/feedback.py

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.feedback import Feedback
from app.models.lesson import Lesson

router = APIRouter()


class FeedbackInput(BaseModel):
    lesson_id: int
    type: str = "general"
    comment: str = ""
    rating: int = 5


@router.post("/feedback/", summary="Оставить отзыв на урок")
def leave_feedback(payload: FeedbackInput, db: Session = Depends(get_db)):
    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first()
    if not lesson:
        raise HTTPException(404, "❌ Урок не найден")

    fb = Feedback(**payload.dict())
    db.add(fb)
    db.commit()
    return {"message": "✅ Отзыв сохранён"}
