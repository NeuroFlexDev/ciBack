from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course_version import CourseVersion
from app.models.lesson_version import LessonVersion
from app.models.module_version import ModuleVersion
from app.models.user import User
from app.schemas.versioning import (
    CourseVersionResponse,
    LessonVersionResponse,
    ModuleVersionResponse,
    RestoreCourseVersionResponse,
)
from app.services.auth import get_current_user_dep
from app.services.versioning_service import create_course_snapshot, restore_course_snapshot

router = APIRouter()


@router.get("/versions/course/{course_id}", response_model=list[CourseVersionResponse])
def get_course_versions(course_id: int, db: Session = Depends(get_db)):
    return (
        db.query(CourseVersion)
        .filter(CourseVersion.course_id == course_id, CourseVersion.is_deleted == False)
        .order_by(CourseVersion.created_at.desc(), CourseVersion.id.desc())
        .all()
    )


@router.post("/versions/course/{course_id}/snapshot", response_model=CourseVersionResponse)
def snapshot_course_version(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    return create_course_snapshot(db, course_id=course_id, user=user)


@router.post(
    "/versions/course-version/{course_version_id}/restore",
    response_model=RestoreCourseVersionResponse,
)
def restore_course_version(
    course_version_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    course = restore_course_snapshot(db, course_version_id=course_version_id, user=user)
    return RestoreCourseVersionResponse(
        ok=True,
        course_id=course.id,
        course_version_id=course_version_id,
    )


@router.get("/versions/course-version/{course_version_id}/modules", response_model=list[ModuleVersionResponse])
def get_module_versions(course_version_id: int, db: Session = Depends(get_db)):
    return (
        db.query(ModuleVersion)
        .filter(ModuleVersion.course_version_id == course_version_id, ModuleVersion.is_deleted == False)
        .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
        .all()
    )


@router.get("/versions/module-version/{module_version_id}/lessons", response_model=list[LessonVersionResponse])
def get_lesson_versions(module_version_id: int, db: Session = Depends(get_db)):
    return (
        db.query(LessonVersion)
        .filter(LessonVersion.module_version_id == module_version_id, LessonVersion.is_deleted == False)
        .order_by(LessonVersion.created_at.desc(), LessonVersion.id.desc())
        .all()
    )


@router.get("/versions/module/{module_id}", include_in_schema=False)
def get_module_versions_legacy(module_id: int):
    raise HTTPException(
        status_code=410,
        detail=(
            "Module version history is available via "
            "/api/versions/course-version/{course_version_id}/modules. "
            f"Source module_id={module_id} is not tracked in the current schema."
        ),
    )


@router.get("/versions/lesson/{lesson_id}", include_in_schema=False)
def get_lesson_versions_legacy(lesson_id: int):
    raise HTTPException(
        status_code=410,
        detail=(
            "Lesson version history is available via "
            "/api/versions/module-version/{module_version_id}/lessons. "
            f"Source lesson_id={lesson_id} is not tracked in the current schema."
        ),
    )
