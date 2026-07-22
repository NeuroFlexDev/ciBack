# app/routes/graph.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.graph_service import build_course_graph

router = APIRouter()


@router.get("/graph", summary="Графовая визуализация курса")
def get_graph(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = db.query(Course).filter(
        Course.id == course_id, Course.owner_id == current_user.id
    ).first()
    if course is None:
        raise HTTPException(status_code=404, detail="Курс не найден")
    return build_course_graph(course_id, db)
