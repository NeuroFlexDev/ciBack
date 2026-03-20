import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.repositories.lesson import LessonRepository
from app.schemas.lesson import LessonCreate, LessonResponse, LessonUpdate
from app.services.access_control import ensure_can_manage_lesson, ensure_can_manage_module
from app.services.auth import get_current_user_dep

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/courses/{course_id}/modules/{module_id}/lessons/",
    response_model=LessonResponse,
    summary="Добавление урока в модуль",
)
def add_lesson(
    course_id: int,
    module_id: int,
    lesson: LessonCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    try:
        module = LessonRepository.get_module(db, course_id, module_id)
        if not module:
            raise HTTPException(status_code=404, detail="Module not found for given course")
        ensure_can_manage_module(user, module)
        new_lesson = LessonRepository.add_lesson(db, course_id, module_id, lesson)
        logger.info(
            "Добавлен новый урок: ID=%s в модуль ID=%s (Курс ID=%s)",
            new_lesson.id,
            module_id,
            course_id,
        )
        return LessonResponse(
            id=new_lesson.id,
            title=new_lesson.title,
            description=new_lesson.description,
            module_id=new_lesson.module_id,
            is_deleted=new_lesson.is_deleted,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/courses/{course_id}/modules/{module_id}/lessons/",
    response_model=list[LessonResponse],
    summary="Получение уроков модуля",
)
def get_lessons(course_id: int, module_id: int, db: Session = Depends(get_db)):
    try:
        lessons = LessonRepository.get_lessons(db, course_id, module_id)
        return [
            LessonResponse(
                id=lesson.id,
                title=lesson.title,
                description=lesson.description,
                module_id=lesson.module_id,
                is_deleted=lesson.is_deleted,
            )
            for lesson in lessons
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/lessons/{lesson_id}",
    response_model=LessonResponse,
    summary="Получение урока по ID",
)
def get_lesson(lesson_id: int, db: Session = Depends(get_db)):
    lesson = LessonRepository.get_lesson(db, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return LessonResponse(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        module_id=lesson.module_id,
        is_deleted=lesson.is_deleted,
    )


@router.put("/lessons/{lesson_id}", response_model=LessonResponse, summary="Обновление урока")
def update_lesson(
    lesson_id: int,
    lesson_update: LessonUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    lesson = LessonRepository.get_lesson(db, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    ensure_can_manage_lesson(user, lesson)
    update_data = lesson_update.model_dump(exclude_unset=True)
    lesson = LessonRepository.update_lesson(db, lesson, update_data)
    logger.info("Обновлен урок: ID=%s", lesson.id)
    return LessonResponse(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        module_id=lesson.module_id,
        is_deleted=lesson.is_deleted,
    )


@router.delete("/lessons/{lesson_id}", summary="Удаление урока")
def delete_lesson(
    lesson_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    lesson = LessonRepository.get_lesson(db, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    ensure_can_manage_lesson(user, lesson)
    LessonRepository.delete_lesson(db, lesson)
    logger.info("Удален урок: ID=%s", lesson_id)
    return {"message": f"Lesson with ID {lesson_id} successfully deleted."}
