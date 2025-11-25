# app/routes/feedback.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.feedback import FeedbackInput
from app.repositories.feedback import FeedbackRepository

router = APIRouter()

@router.post("/feedback/", summary="Оставить отзыв на урок")
def leave_feedback(payload: FeedbackInput, db: Session = Depends(get_db)):
    try:
        FeedbackRepository.save_feedback(db, payload)
        return {"message": "✅ Отзыв сохранён"}
    except ValueError as e:
        raise HTTPException(404, str(e))
