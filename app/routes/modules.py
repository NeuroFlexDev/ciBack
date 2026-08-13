from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.module import Module
from app.models.user import User
from app.schemas.course_editor import ModuleCreateEditor, ModuleUpdateEditor, OrderUpdate
from app.schemas.course_review import ModuleDetail
from app.services.auth_service import get_current_user
from app.services.course_editor_service import CourseEditorService
from app.services.course_review_service import CourseReviewService

router = APIRouter()


def _out(item: Module) -> dict:
    return {"id": item.id, "title": item.title, "course_id": item.course_id, "position": item.position, "revision": item.revision}


@router.post("/courses/{course_id}/modules/", status_code=201)
def add_module(course_id: int, payload: ModuleCreateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService.create_module(db, course_id, current_user.id, payload.title))


@router.get("/courses/{course_id}/modules/")
def get_modules(course_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService._course(db, course_id, current_user.id)
    return [_out(item) for item in db.query(Module).filter(Module.course_id == course_id, Module.is_deleted.is_(False)).order_by(Module.position, Module.id)]


@router.put("/courses/{course_id}/modules/order")
def reorder_modules(course_id: int, payload: OrderUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService._course(db, course_id, current_user.id, True)
    rows = CourseEditorService.reorder(db, entity=Module, parent_filter=(Module.course_id == course_id,), items=payload.items, owner_id=current_user.id, snapshot=CourseEditorService._module_snapshot)
    return [_out(item) for item in rows]


@router.get("/modules/{module_id}", response_model=ModuleDetail)
def get_module(module_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return CourseReviewService.module_detail(db, module_id, current_user.id)


@router.put("/modules/{module_id}")
def update_module(module_id: int, payload: ModuleUpdateEditor, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _out(CourseEditorService.update_module(db, module_id, current_user.id, payload.title, payload.expected_revision))


@router.delete("/modules/{module_id}")
def delete_module(module_id: int, expected_revision: int = Query(gt=0), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    CourseEditorService.delete(db, kind="module", item_id=module_id, owner_id=current_user.id, expected=expected_revision)
    return {"message": "Module deleted"}
