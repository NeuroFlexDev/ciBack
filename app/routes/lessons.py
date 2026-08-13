from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.user import User
from app.schemas.course_editor import LessonCreateEditor, LessonUpdateEditor, OrderUpdate
from app.services.auth_service import get_current_user
from app.services.course_editor_service import CourseEditorService

router = APIRouter()


def _out(item: Lesson) -> dict:
    content = item.theory.content if item.theory and not item.theory.is_deleted else ""
    return {"id": item.id, "title": item.title, "description": item.description or "", "content": content, "module_id": item.module_id, "position": item.position, "revision": item.revision}


@router.post("/courses/{course_id}/modules/{module_id}/lessons/", status_code=201)
def add_lesson(course_id: int, module_id: int, payload: LessonCreateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService.create_lesson(db, course_id, module_id, current_user.id, payload.title, payload.description, payload.content))


@router.get("/courses/{course_id}/modules/{module_id}/lessons/")
def get_lessons(course_id: int, module_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    module = CourseEditorService._module(db, module_id, current_user.id)
    if module.course_id != course_id: from fastapi import HTTPException; raise HTTPException(404, "Module not found")
    return [_out(item) for item in sorted((x for x in module.lessons if not x.is_deleted), key=lambda x: (x.position, x.id))]


@router.put("/modules/{module_id}/lessons/order")
def reorder_lessons(module_id: int, payload: OrderUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService._module(db, module_id, current_user.id, True)
    rows = CourseEditorService.reorder(db, entity=Lesson, parent_filter=(Lesson.module_id == module_id,), items=payload.items, owner_id=current_user.id, snapshot=CourseEditorService._lesson_snapshot)
    return [_out(item) for item in rows]


@router.get("/lessons/{lesson_id}")
def get_lesson(lesson_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService._lesson(db, lesson_id, current_user.id))


@router.put("/lessons/{lesson_id}")
def update_lesson(lesson_id: int, payload: LessonUpdateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService.update_lesson(db, lesson_id, current_user.id, payload.model_dump(exclude={"expected_revision"}, exclude_unset=True), payload.expected_revision))


@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, expected_revision: int = Query(gt=0), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService.delete(db, kind="lesson", item_id=lesson_id, owner_id=current_user.id, expected=expected_revision)
    return {"message": "Lesson deleted"}
