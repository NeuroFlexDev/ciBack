from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course_version import CourseVersion
from app.models.lesson_version import LessonVersion
from app.models.module_version import ModuleVersion

router = APIRouter()


@router.get("/versions/course/{course_id}")
def get_course_versions(course_id: int, db: Session = Depends(get_db)):
    return (
        db.query(CourseVersion)
        .filter(CourseVersion.course_id == course_id)
        .order_by(CourseVersion.created_at.desc())
        .all()
    )


@router.get("/versions/module/{module_id}")
def get_module_versions(module_id: int, db: Session = Depends(get_db)):
    return (
        db.query(ModuleVersion)
        .filter(ModuleVersion.module_id == module_id)
        .order_by(ModuleVersion.created_at.desc())
        .all()
    )


@router.get("/versions/lesson/{lesson_id}")
def get_lesson_versions(lesson_id: int, db: Session = Depends(get_db)):
    return (
        db.query(LessonVersion)
        .filter(LessonVersion.lesson_id == lesson_id)
        .order_by(LessonVersion.created_at.desc())
        .all()
    )
