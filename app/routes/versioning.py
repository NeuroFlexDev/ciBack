from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.lesson import Lesson
from app.models.lesson_version import LessonVersion
from app.models.module import Module
from app.models.module_version import ModuleVersion
from app.models.test import Test
from app.models.test_version import TestVersion
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter()


def _owned(db, model, item_id, owner_id):
    if model is Module:
        return db.query(Module).join(Course).filter(Module.id == item_id, Course.owner_id == owner_id).first()
    if model is Lesson:
        return db.query(Lesson).join(Module).join(Course).filter(Lesson.id == item_id, Course.owner_id == owner_id).first()
    return db.query(Test).outerjoin(Module, Test.module_id == Module.id).join(Course, (Module.course_id == Course.id) | (Test.course_id == Course.id)).filter(Test.id == item_id, Course.owner_id == owner_id).first()


@router.get("/versions/course/{course_id}")
def get_course_versions(course_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not db.query(Course).filter(Course.id == course_id, Course.owner_id == current_user.id).first(): raise HTTPException(404, "Course not found")
    return db.query(CourseVersion).filter(CourseVersion.course_id == course_id).order_by(CourseVersion.created_at.desc()).all()


@router.get("/versions/module/{module_id}")
def get_module_versions(module_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not _owned(db, Module, module_id, current_user.id): raise HTTPException(404, "Module not found")
    return db.query(ModuleVersion).filter(ModuleVersion.module_id == module_id).order_by(ModuleVersion.revision.desc()).all()


@router.get("/versions/lesson/{lesson_id}")
def get_lesson_versions(lesson_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not _owned(db, Lesson, lesson_id, current_user.id): raise HTTPException(404, "Lesson not found")
    return db.query(LessonVersion).filter(LessonVersion.lesson_id == lesson_id).order_by(LessonVersion.revision.desc()).all()


@router.get("/versions/test/{test_id}")
def get_test_versions(test_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not _owned(db, Test, test_id, current_user.id): raise HTTPException(404, "Test not found")
    return db.query(TestVersion).filter(TestVersion.test_id == test_id).order_by(TestVersion.revision.desc()).all()
