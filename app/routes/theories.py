# app/routes/theories.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.theory import Theory

router = APIRouter()


class TheoryCreate(BaseModel):
    lesson_id: int
    content: str


class TheoryUpdate(BaseModel):
    content: str


@router.post("/theories/", summary="Создание теоретического блока")
def create_theory(payload: TheoryCreate, db: Session = Depends(get_db)):
    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first()
    if not lesson:
        raise HTTPException(404, "Урок не найден")

    existing = db.query(Theory).filter(Theory.lesson_id == payload.lesson_id).first()
    if existing:
        raise HTTPException(400, "Теория уже существует для этого урока")

    theory = Theory(lesson_id=payload.lesson_id, content=payload.content)
    db.add(theory)
    db.commit()
    db.refresh(theory)
    return theory


@router.get("/theories/{lesson_id}", summary="Получение теории по ID урока")
def get_theory_by_lesson(lesson_id: int, db: Session = Depends(get_db)):
    theory = db.query(Theory).filter(Theory.lesson_id == lesson_id).first()
    if not theory:
        raise HTTPException(404, "Теория не найдена")
    return theory


@router.put("/theories/{lesson_id}", summary="Обновление теории")
def update_theory(lesson_id: int, payload: TheoryUpdate, db: Session = Depends(get_db)):
    theory = db.query(Theory).filter(Theory.lesson_id == lesson_id).first()
    if not theory:
        raise HTTPException(404, "Теория не найдена")

    theory.content = payload.content
    db.commit()
    return {"message": "Теория обновлена"}


@router.delete("/theories/{lesson_id}", summary="Удаление теории")
def delete_theory(lesson_id: int, db: Session = Depends(get_db)):
    theory = db.query(Theory).filter(Theory.lesson_id == lesson_id).first()
    if not theory:
        raise HTTPException(404, "Теория не найдена")

    db.delete(theory)
    db.commit()
    return {"message": "Теория удалена"}
