from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.theory import Theory
from app.services.agents.course_agent import get_course_agent

router = APIRouter()

@router.get("/agent/improve-theory", summary="Агент: улучшение теории по lesson_id и цели")
def improve_theory(lesson_id: int = Query(...), goal: str = Query(...), db: Session = Depends(get_db)):
    # Проверяем существование урока и теории
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(404, detail="❌ Урок не найден")

    theory = db.query(Theory).filter(Theory.lesson_id == lesson_id).first()
    if not theory:
        raise HTTPException(404, detail="❌ Теория для урока не найдена")

    agent = get_course_agent()
    result = agent.invoke({
        "lesson_id": lesson_id,
        "theory": theory.content,
        "goal": goal
    })

    return {
        "original": theory.content,
        "improved": result
    }
