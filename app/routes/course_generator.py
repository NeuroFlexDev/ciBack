from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.course_structure import CourseStructure
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter()

class LessonRequest(BaseModel):
    lesson_id: int

class ModuleLessonGenerationRequest(BaseModel):
    module_id: int
    module_title: str


@router.get("/courses/{course_id}/generate_modules", deprecated=True, summary="Устаревшая генерация и сохранение модулей курса")
def generate_and_save_modules(
    course_id: int,
    cs_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = db.query(Course).filter(Course.id == course_id, Course.owner_id == current_user.id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = (
        db.query(CourseStructure)
        .filter(CourseStructure.id == cs_id, CourseStructure.owner_id == current_user.id)
        .first()
    )
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    raise HTTPException(410, "Используйте POST /api/courses/{course_id}/generation-runs. Старый генератор отключён: он не сохранял версии и проверки качества.")


@router.post("/courses/{course_id}/generate_module_lessons", deprecated=True, summary="Устаревшая генерация уроков модуля с сохранением")
def generate_and_save_module_lessons(
    course_id: int,
    cs_id: int = Query(...),
    payload: ModuleLessonGenerationRequest = Depends(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = db.query(Course).filter(Course.id == course_id, Course.owner_id == current_user.id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = (
        db.query(CourseStructure)
        .filter(CourseStructure.id == cs_id, CourseStructure.owner_id == current_user.id)
        .first()
    )
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    raise HTTPException(410, "Используйте POST /api/courses/{course_id}/generation-runs. Старый генератор отключён: он не сохранял версии и проверки качества.")


@router.post("/courses/{course_id}/generate_lesson_content", deprecated=True, summary="Устаревшая генерация контента урока")
def generate_and_save_lesson_content(
    course_id: int,
    payload: LessonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = db.query(Course).filter(Course.id == course_id, Course.owner_id == current_user.id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    lesson = (
        db.query(Lesson)
        .join(Module, Lesson.module_id == Module.id)
        .join(Course, Module.course_id == Course.id)
        .filter(Lesson.id == payload.lesson_id, Course.id == course_id, Course.owner_id == current_user.id)
        .first()
    )
    if not lesson:
        raise HTTPException(404, "❌ Урок не найден")

    raise HTTPException(410, "Используйте POST /api/courses/{course_id}/generation-runs. Старый генератор отключён: он не сохранял версии и проверки качества.")
