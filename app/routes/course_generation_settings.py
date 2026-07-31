from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.course_generation_settings import (
    CourseGenerationSettingsResponse,
    CourseGenerationSettingsUpdate,
)
from app.services.auth_service import get_current_user
from app.services.course_generation_settings_service import (
    CourseGenerationSettingsService,
)


router = APIRouter(prefix="/courses")


@router.get(
    "/{course_id}/generation-settings",
    response_model=CourseGenerationSettingsResponse,
)
def get_generation_settings(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return CourseGenerationSettingsService.get(db, course_id, current_user.id)


@router.put(
    "/{course_id}/generation-settings",
    response_model=CourseGenerationSettingsResponse,
)
def put_generation_settings(
    course_id: int,
    payload: CourseGenerationSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return CourseGenerationSettingsService.upsert(
        db, course_id, current_user.id, payload
    )
