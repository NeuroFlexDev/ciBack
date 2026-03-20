# app/routes/courses.py
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.repositories.course import CourseRepository
from app.schemas.course import CourseCreate, CourseResponse, CourseUpdate
from app.services.access_control import ensure_can_manage_course
from app.services.auth import get_current_user_dep

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/courses/", summary="Создание курса", response_model=CourseResponse)
def create_course(
    course: CourseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    try:
        logger.info("Получен запрос на создание курса: %s", course.model_dump_json())
        new_course = CourseRepository.create(db, course, owner_id=user.id)
        logger.info("Курс успешно создан: ID=%s, Название=%s", new_course.id, new_course.name)
        return CourseResponse(
            id=new_course.id,
            title=new_course.name,
            description=new_course.description,
            level=new_course.level,
            language=new_course.language,
            is_deleted=new_course.is_deleted,
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Ошибка при создании курса: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при создании курса") from exc


@router.get("/courses/", summary="Получение всех курсов", response_model=list[CourseResponse])
def get_all_courses(db: Session = Depends(get_db)):
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
    except Exception as exc:
        logger.error("Ошибка при получении курсов: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при получении курсов") from exc


@router.put("/courses/{course_id}", summary="Обновление курса", response_model=CourseResponse)
def update_course(
    course_id: int,
    course_update: CourseUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    try:
        course = CourseRepository.get_by_id(db, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Курс не найден")
        ensure_can_manage_course(user, course)

        update_data = course_update.model_dump(exclude_unset=True)
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
    except Exception as exc:
        db.rollback()
        logger.error("Ошибка при обновлении курса: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при обновлении курса") from exc


@router.delete("/courses/{course_id}", summary="Удаление курса")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    try:
        course = CourseRepository.get_by_id(db, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Курс не найден")
        ensure_can_manage_course(user, course)
        CourseRepository.delete(db, course)
        logger.info("Курс удален: ID=%s", course_id)
        return {"message": f"Курс с ID {course_id} успешно удален"}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Ошибка при удалении курса: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при удалении курса") from exc
