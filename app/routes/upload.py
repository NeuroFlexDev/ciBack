# app/routes/upload.py
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.repositories.course import CourseRepository
from app.schemas.upload import LessonRequest
from app.services.access_control import ensure_can_manage_course
from app.services.auth import get_current_user_dep
from app.services.upload_service import UploadService

router = APIRouter()


@router.post("/courses/{course_id}/generate_lesson_content", summary="Генерация контента урока")
def generate_and_save_lesson_content(
    course_id: int,
    payload: LessonRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    course = CourseRepository.get_by_id(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    ensure_can_manage_course(user, course)
    return UploadService.generate_and_save_lesson_content(db, course_id, payload.lesson_id)


@router.post("/courses/{course_id}/upload-description", summary="RAG: загрузка файла и обновление описания курса")
def upload_and_update_description(
    course_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    course = CourseRepository.get_by_id(db, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    ensure_can_manage_course(user, course)
    return UploadService.upload_and_update_description(db, course_id, file)
