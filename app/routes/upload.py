"""Legacy endpoint retained only as an authenticated migration response."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.services.auth_service import get_current_user
from app.services.course_editor_service import CourseEditorService
router = APIRouter()

@router.post("/courses/{course_id}/upload-description", deprecated=True)
def upload_and_update_description(course_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    CourseEditorService._course(db, course_id, user.id)
    raise HTTPException(410, {"code": "legacy_upload_retired",
        "message": "Загрузите источник через мастер курса. Этот старый путь не поддерживал версии, лимиты и проверку материалов.",
        "upload_url": f"/api/courses/{course_id}/documents"})
