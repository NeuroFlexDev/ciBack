# app/routes/lesson.py

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.lesson import LessonCreate, LessonUpdate, LessonResponse
from app.repositories.lesson import LessonRepository

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "/courses/{course_id}/modules/{module_id}/lessons/",
    response_model=LessonResponse,
    summary="Добавление урока в модуль",
)
def add_lesson(course_id: int, module_id: int, lesson: LessonCreate, db: Session = Depends(get_db)):
    """
    Добавляет новый урок к модулю конкретного курса.
    Проверяет, что модуль существует и принадлежит указанному курсу.
    """
    try:
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
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get(
    "/courses/{course_id}/modules/{module_id}/lessons/",
    response_model=list[LessonResponse],
    summary="Получение уроков модуля",
)
def get_lessons(course_id: int, module_id: int, db: Session = Depends(get_db)):
    """
    Возвращает список всех уроков для модуля конкретного курса.
    """
    try:
        lessons = LessonRepository.get_lessons(db, course_id, module_id)
        return [
            LessonResponse(
                id=lesson.id,
                title=lesson.title,
                description=lesson.description,
                module_id=lesson.module_id,
            )
            for lesson in lessons
        ]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get(
    "/lessons/{lesson_id}",
    response_model=LessonResponse,
    summary="Получение урока по ID",
)
def get_lesson(lesson_id: int, db: Session = Depends(get_db)):
    """
    Возвращает данные урока по его ID.
    """
    lesson = LessonRepository.get_lesson(db, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return LessonResponse(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        module_id=lesson.module_id,
    )

@router.put("/lessons/{lesson_id}", response_model=LessonResponse, summary="Обновление урока")
def update_lesson(lesson_id: int, lesson_update: LessonUpdate, db: Session = Depends(get_db)):
    """
    Обновляет данные урока по указанному ID.
    """
    lesson = LessonRepository.get_lesson(db, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    update_data = lesson_update.dict(exclude_unset=True)
    lesson = LessonRepository.update_lesson(db, lesson, update_data)
    logger.info("Обновлен урок: ID=%s", lesson.id)
    return LessonResponse(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        module_id=lesson.module_id,
    )

@router.delete("/lessons/{lesson_id}", summary="Удаление урока")
def delete_lesson(lesson_id: int, db: Session = Depends(get_db)):
    """
    Удаляет урок по его ID.
    """
    lesson = LessonRepository.get_lesson(db, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    LessonRepository.delete_lesson(db, lesson)
    logger.info("Удален урок: ID=%s", lesson_id)
    return {"message": f"Lesson with ID {lesson_id} successfully deleted."}
