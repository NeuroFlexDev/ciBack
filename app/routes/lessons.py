# app/routes/lessons.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.module import Module

router = APIRouter()

@router.post("/modules/{module_id}/lessons/")
def add_lesson(module_id: int, lesson: str, description: str, db: Session = Depends(get_db)):
    """
    Добавляет урок к конкретному модулю.
    """
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    new_lesson = Lesson(
        lesson=lesson,
        description=description,
        module_id=module.id
    )
    db.add(new_lesson)
    db.commit()
    db.refresh(new_lesson)
    return {"message": "Lesson added", "lesson": new_lesson}

@router.get("/modules/{module_id}/lessons/")
def get_lessons(module_id: int, db: Session = Depends(get_db)):
    """
    Получить все уроки конкретного модуля.
    """
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module.lessons
