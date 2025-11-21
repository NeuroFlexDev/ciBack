# app/routes/upload.py
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.upload import LessonRequest
from app.services.upload_service import UploadService

router = APIRouter()

@router.post("/courses/{course_id}/generate_lesson_content", summary="Генерация контента урока")
def generate_and_save_lesson_content(course_id: int, payload: LessonRequest, db: Session = Depends(get_db)):
    return UploadService.generate_and_save_lesson_content(db, course_id, payload.lesson_id)

@router.post("/courses/{course_id}/upload-description", summary="RAG: загрузка файла и обновление описания курса")
def upload_and_update_description(course_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    return UploadService.upload_and_update_description(db, course_id, file)
