from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.user import User
from app.repositories.theory import TheoryRepository
from app.schemas.common import MessageResponse
from app.schemas.theory import TheoryCreate, TheoryResponse, TheoryUpdate
from app.services.access_control import ensure_can_manage_lesson, ensure_can_manage_theory
from app.services.auth import get_current_user_dep

router = APIRouter()


@router.post("/theories/", summary="Создание теории", response_model=TheoryResponse)
def create_theory(
    payload: TheoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id, Lesson.is_deleted == False).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Урок не найден")
    ensure_can_manage_lesson(user, lesson)
    try:
        return TheoryRepository.create_theory(db, payload)
    except ValueError as exc:
        detail = str(exc)
        status_code = 400 if "существует" in detail.lower() else 404
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/theories/{lesson_id}", summary="Получение теории по lesson_id", response_model=TheoryResponse)
def get_theory_by_lesson(lesson_id: int, db: Session = Depends(get_db)):
    theory = TheoryRepository.get_theory_by_lesson(db, lesson_id)
    if not theory:
        raise HTTPException(status_code=404, detail="Theory not found")
    return theory


@router.put("/theories/{lesson_id}", summary="Обновление теории", response_model=TheoryResponse)
def update_theory(
    lesson_id: int,
    payload: TheoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    theory = TheoryRepository.get_theory_by_lesson(db, lesson_id)
    if not theory:
        raise HTTPException(status_code=404, detail="Theory not found")
    ensure_can_manage_theory(user, theory)
    return TheoryRepository.update_theory(db, theory, payload)


@router.delete("/theories/{lesson_id}", summary="Удаление теории", response_model=MessageResponse)
def delete_theory(
    lesson_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    theory = TheoryRepository.get_theory_by_lesson(db, lesson_id)
    if not theory:
        raise HTTPException(status_code=404, detail="Theory not found")
    ensure_can_manage_theory(user, theory)
    TheoryRepository.delete_theory(db, theory)
    return MessageResponse(message="Theory deleted")
