from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.theory import Theory
from app.models.user import User
from app.agents.course_agent import get_course_agent
from app.services.auth_service import get_current_user

router = APIRouter()


@router.get("/agent/improve-theory", summary="Агент: улучшение теории по lesson_id и цели")
def improve_theory(
    lesson_id: int = Query(...),
    goal: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Проверяем существование урока и теории
    lesson = (
        db.query(Lesson)
        .join(Module, Lesson.module_id == Module.id)
        .join(Course, Module.course_id == Course.id)
        .filter(Lesson.id == lesson_id, Course.owner_id == current_user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(404, detail="❌ Урок не найден")

    theory = db.query(Theory).filter(Theory.lesson_id == lesson_id).first()
    if not theory:
        raise HTTPException(404, detail="❌ Теория для урока не найдена")

    agent = get_course_agent(db)
    result = agent["improve_theory"](lesson_id, goal)

    return {"original": theory.content, "improved": result}
