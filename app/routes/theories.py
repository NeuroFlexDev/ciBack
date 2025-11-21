# app/routes/theories.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.theory import TheoryCreate, TheoryUpdate, TheoryResponse
from app.repositories.theory import TheoryRepository

router = APIRouter()

@router.post("/theories/", summary="Создание теоретического блока", response_model=TheoryResponse)
def create_theory(payload: TheoryCreate, db: Session = Depends(get_db)):
    try:
        theory = TheoryRepository.create_theory(db, payload)
        return TheoryResponse(lesson_id=theory.lesson_id, content=theory.content)
    except ValueError as e:
        raise HTTPException(400 if "существует" in str(e) else 404, str(e))

@router.get("/theories/{lesson_id}", summary="Получение теории по ID урока", response_model=TheoryResponse)
def get_theory_by_lesson(lesson_id: int, db: Session = Depends(get_db)):
    theory = TheoryRepository.get_theory_by_lesson(db, lesson_id)
    if not theory:
        raise HTTPException(404, "Теория не найдена")
    return TheoryResponse(lesson_id=theory.lesson_id, content=theory.content)

@router.put("/theories/{lesson_id}", summary="Обновление теории", response_model=TheoryResponse)
def update_theory(lesson_id: int, payload: TheoryUpdate, db: Session = Depends(get_db)):
    theory = TheoryRepository.get_theory_by_lesson(db, lesson_id)
    if not theory:
        raise HTTPException(404, "Теория не найдена")
    theory = TheoryRepository.update_theory(db, theory, payload)
    return TheoryResponse(lesson_id=theory.lesson_id, content=theory.content)

@router.delete("/theories/{lesson_id}", summary="Удаление теории")
def delete_theory(lesson_id: int, db: Session = Depends(get_db)):
    theory = TheoryRepository.get_theory_by_lesson(db, lesson_id)
    if not theory:
        raise HTTPException(404, "Теория не найдена")
    TheoryRepository.delete_theory(db, theory)
    return {"message": "Теория удалена"}
