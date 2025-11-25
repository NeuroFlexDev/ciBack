# app/routes/courses.py
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.schemas.course import CourseCreate, CourseUpdate, CourseResponse
from app.repositories.course import CourseRepository
from app.database.db import get_db

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/courses/", summary="Создание курса", response_model=CourseResponse)
def create_course(course: CourseCreate, db: Session = Depends(get_db)):
    """
    Создает курс в базе данных.
    """
    try:
        logger.info("Получен запрос на создание курса: %s", course.json())
        new_course = CourseRepository.create(db, course)
        logger.info("Курс успешно создан: ID=%s, Название=%s", new_course.id, new_course.name)
        return CourseResponse(
            id=new_course.id,
            title=new_course.name,
            description=new_course.description,
            level=new_course.level,
            language=new_course.language,
            is_deleted=new_course.is_deleted,
        )
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при создании курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при создании курса")

@router.get("/courses/", summary="Получение всех курсов", response_model=list[CourseResponse])
def get_all_courses(db: Session = Depends(get_db)):
    """
    Возвращает список всех курсов, сохраненных в базе данных.
    """
    try:
        courses = CourseRepository.list_all(db)
        return [
            CourseResponse(
                id=course.id,
                title=course.name,
                description=course.description,
                level=course.level,
                language=course.language,
                is_deleted=course.is_deleted,
            )
            for course in courses
        ]
    except Exception as e:
        logger.error("Ошибка при получении курсов: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при получении курсов")

@router.put("/courses/{course_id}", summary="Обновление курса", response_model=CourseResponse)
def update_course(course_id: int, course_update: CourseUpdate, db: Session = Depends(get_db)):
    """
    Обновляет данные курса по указанному ID.
    """
    try:
        course = CourseRepository.get_by_id(db, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Курс не найден")

        update_data = course_update.dict(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="Нет данных для обновления")

        course = CourseRepository.update(db, course, update_data)
        logger.info("Курс обновлен: ID=%s, Обновленные данные: %s", course.id, update_data)
        return CourseResponse(
            id=course.id,
            title=course.name,
            description=course.description,
            level=course.level,
            language=course.language,
            is_deleted=course.is_deleted,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при обновлении курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при обновлении курса")

@router.delete("/courses/{course_id}", summary="Удаление курса")
def delete_course(course_id: int, db: Session = Depends(get_db)):
    """
    Удаляет курс по указанному ID.
    """
    try:
        course = CourseRepository.get_by_id(db, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Курс не найден")
        CourseRepository.delete(db, course)
        logger.info("Курс удален: ID=%s", course_id)
        return {"message": f"Курс с ID {course_id} успешно удален"}
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при удалении курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при удалении курса")
