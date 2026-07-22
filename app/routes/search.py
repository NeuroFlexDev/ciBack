# app/routes/search.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.embedding_service import search

router = APIRouter()


@router.get("/search", summary="Семантический поиск по контенту курса")
def semantic_search(
    q: str = Query(..., description="Поисковый запрос"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lesson_ids = {
        row[0]
        for row in db.query(Lesson.id)
        .join(Module, Lesson.module_id == Module.id)
        .join(Course, Module.course_id == Course.id)
        .filter(Course.owner_id == current_user.id)
        .all()
    }
    results = search(q, allowed_lesson_ids=lesson_ids)
    return {"results": results}
