# app/routes/graph.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.services.graph_service import build_course_graph

router = APIRouter()

@router.get("/graph", summary="Графовая визуализация курса")
def get_graph(course_id: int, db: Session = Depends(get_db)):
    return build_course_graph(course_id, db)
