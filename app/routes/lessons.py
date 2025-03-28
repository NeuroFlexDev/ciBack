import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.module import Module
from app.models.lesson import Lesson

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic-модель для создания урока
class LessonCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Название урока")
    description: str = Field(..., min_length=1, description="Описание урока")

# Pydantic-модель для обновления урока (поля опциональные)
class LessonUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Новое название урока")
    description: Optional[str] = Field(None, min_length=1, description="Новое описание урока")

# Pydantic-модель для ответа
class LessonResponse(BaseModel):
    id: int
    title: str
    description: str
    module_id: int

    class Config:
        orm_mode = True

@router.post("/courses/{course_id}/modules/{module_id}/lessons/", response_model=LessonResponse, summary="Добавление урока в модуль")
def add_lesson(course_id: int, module_id: int, lesson: LessonCreate, db: Session = Depends(get_db)):
    """
    Добавляет новый урок к модулю конкретного курса.
    Проверяет, что модуль существует и принадлежит указанному курсу.
    """
    # Здесь можно добавить дополнительную проверку, что модуль действительно относится к курсу course_id,
    # если в модели Module есть поле course_id.
    module = db.query(Module).filter(Module.id == module_id, Module.course_id == course_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found for given course")

    new_lesson = Lesson(
        title=lesson.title,         # Используем поле title, так как в модели Lesson его так и называют
        description=lesson.description,
        module_id=module.id
    )
    db.add(new_lesson)
    db.commit()
    db.refresh(new_lesson)
    logger.info("Добавлен новый урок: ID=%s в модуль ID=%s (Курс ID=%s)", new_lesson.id, module_id, course_id)
    return LessonResponse(
        id=new_lesson.id,
        title=new_lesson.title,
        description=new_lesson.description,
        module_id=new_lesson.module_id
    )

@router.get("/courses/{course_id}/modules/{module_id}/lessons/", response_model=List[LessonResponse], summary="Получение уроков модуля")
def get_lessons(course_id: int, module_id: int, db: Session = Depends(get_db)):
    """
    Возвращает список всех уроков для модуля конкретного курса.
    """
    module = db.query(Module).filter(Module.id == module_id, Module.course_id == course_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found for given course")
    
    lessons = module.lessons  # Предполагается, что у Module есть отношение lessons
    return [
        LessonResponse(
            id=lesson.id,
            title=lesson.title,
            description=lesson.description,
            module_id=lesson.module_id
        )
        for lesson in lessons
    ]

@router.get("/lessons/{lesson_id}", response_model=LessonResponse, summary="Получение урока по ID")
def get_lesson(lesson_id: int, db: Session = Depends(get_db)):
    """
    Возвращает данные урока по его ID.
    """
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return LessonResponse(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        module_id=lesson.module_id
    )

@router.put("/lessons/{lesson_id}", response_model=LessonResponse, summary="Обновление урока")
def update_lesson(lesson_id: int, lesson_update: LessonUpdate, db: Session = Depends(get_db)):
    """
    Обновляет данные урока по указанному ID.
    """
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    update_data = lesson_update.dict(exclude_unset=True)
    if "title" in update_data:
        lesson.title = update_data["title"]
    if "description" in update_data:
        lesson.description = update_data["description"]
    
    db.commit()
    db.refresh(lesson)
    logger.info("Обновлен урок: ID=%s", lesson.id)
    return LessonResponse(
        id=lesson.id,
        title=lesson.title,
        description=lesson.description,
        module_id=lesson.module_id
    )

@router.delete("/lessons/{lesson_id}", summary="Удаление урока")
def delete_lesson(lesson_id: int, db: Session = Depends(get_db)):
    """
    Удаляет урок по его ID.
    """
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    
    db.delete(lesson)
    db.commit()
    logger.info("Удален урок: ID=%s", lesson_id)
    return {"message": f"Lesson with ID {lesson_id} successfully deleted."}
