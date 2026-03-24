from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.repositories.course import CourseRepository
from app.schemas.graph import CourseGraphResponse
from app.services.access_control import ensure_can_manage_course
from app.services.auth import get_current_user_dep
from app.services.graph_service import build_course_graph

router = APIRouter(tags=["Graph"])


@router.get("/graph", summary="Course graph visualization", response_model=CourseGraphResponse)
def get_graph(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    course = CourseRepository.get_by_id(db, course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    ensure_can_manage_course(user, course)
    try:
        return build_course_graph(course_id, db)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
