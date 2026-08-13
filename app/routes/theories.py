from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.course_editor_service import CourseEditorService

router = APIRouter()


class TheoryCreateEditor(BaseModel):
    lesson_id: int = Field(gt=0)
    content: str
    expected_revision: int = Field(gt=0)


class TheoryUpdateEditor(BaseModel):
    content: str
    expected_revision: int = Field(gt=0)


def _out(lesson):
    return {"lesson_id": lesson.id, "content": lesson.theory.content if lesson.theory else "", "revision": lesson.revision}


@router.post("/theories/")
def create_theory(payload: TheoryCreateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lesson = CourseEditorService.update_lesson(db, payload.lesson_id, current_user.id, {"content": payload.content}, payload.expected_revision)
    return _out(lesson)


@router.get("/theories/{lesson_id}")
def get_theory_by_lesson(lesson_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService._lesson(db, lesson_id, current_user.id))


@router.put("/theories/{lesson_id}")
def update_theory(lesson_id: int, payload: TheoryUpdateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService.update_lesson(db, lesson_id, current_user.id, {"content": payload.content}, payload.expected_revision))


@router.delete("/theories/{lesson_id}")
def delete_theory(lesson_id: int, expected_revision: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lesson = CourseEditorService.update_lesson(db, lesson_id, current_user.id, {"content": ""}, expected_revision)
    lesson.theory.is_deleted = True
    db.commit()
    return {"message": "Theory deleted", "revision": lesson.revision}
