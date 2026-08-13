import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.test import Test
from app.models.user import User
from app.schemas.course_editor import OrderUpdate, TestCreateEditor, TestUpdateEditor
from app.services.auth_service import get_current_user
from app.services.course_editor_service import CourseEditorService

router = APIRouter()


def _out(item: Test) -> dict:
    return {"id": item.id, "question": item.question, "answers": json.loads(item.answers or "[]"), "correct_answer": item.correct_answer, "correct": item.correct_answer, "module_id": item.module_id, "course_id": item.course_id, "assessment_scope": item.assessment_scope, "position": item.position, "revision": item.revision}


def _parse(payload: TestCreateEditor) -> tuple[str, list[str], str]:
    if payload.question: return payload.question, payload.answers or [], payload.correct_answer or ""
    description = payload.description or ""
    if "Варианты:" not in description or "(Правильный:" not in description: raise HTTPException(400, "Неверный формат описания теста")
    parts = description.split("Варианты:", 1)[1].split("(Правильный:", 1)
    return payload.test or "", [x.strip() for x in parts[0].split(",") if x.strip()], parts[1].replace(")", "").strip()


@router.post("/modules/{module_id}/tests/", status_code=201)
def add_test(module_id: int, payload: TestCreateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    question, answers, correct = _parse(payload); return _out(CourseEditorService.create_test(db, current_user.id, module_id=module_id, course_id=None, question=question, answers=answers, correct=correct))


@router.post("/courses/{course_id}/tests/", status_code=201)
def add_final_test(course_id: int, payload: TestCreateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    question, answers, correct = _parse(payload); return _out(CourseEditorService.create_test(db, current_user.id, module_id=None, course_id=course_id, question=question, answers=answers, correct=correct))


@router.get("/modules/{module_id}/tests/")
def get_tests(module_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService._module(db, module_id, current_user.id)
    return [_out(x) for x in db.query(Test).filter(Test.module_id == module_id, Test.is_deleted.is_(False)).order_by(Test.position, Test.id)]


@router.put("/modules/{module_id}/tests/order")
def reorder_tests(module_id: int, payload: OrderUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService._module(db, module_id, current_user.id, True)
    rows = CourseEditorService.reorder(db, entity=Test, parent_filter=(Test.module_id == module_id,), items=payload.items, owner_id=current_user.id, snapshot=CourseEditorService._test_snapshot)
    return [_out(x) for x in rows]


@router.get("/tests/{test_id}")
def get_test(test_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService._test(db, test_id, current_user.id))


@router.put("/tests/{test_id}")
def update_test(test_id: int, payload: TestUpdateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService.update_test(db, test_id, current_user.id, payload.model_dump(exclude={"expected_revision"}), payload.expected_revision))


@router.delete("/tests/{test_id}")
def delete_test(test_id: int, expected_revision: int = Query(gt=0), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService.delete(db, kind="test", item_id=test_id, owner_id=current_user.id, expected=expected_revision); return {"message": "Test deleted"}
