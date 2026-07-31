# app/routes/search.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.embedding_service import search
from app.schemas.retrieval import RetrievalResponse
from app.services.embedding_service import get_vector_store
from app.services.retrieval_service import RetrievalService
from app.services.vector_store import VectorStore

router = APIRouter()


@router.get("/search", summary="Семантический поиск по контенту курса")
def semantic_search(
    q: str = Query(..., description="Поисковый запрос"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lesson_ids = {
        row[0]
        for row in db.query(Lesson.id)
        .join(Module, Lesson.module_id == Module.id)
        .join(Course, Module.course_id == Course.id)
        .filter(Course.owner_id == current_user.id)
        .all()
    }
    results = search(q, allowed_lesson_ids=lesson_ids)
    return {"results": results}


@router.get(
    "/courses/{course_id}/retrieval",
    response_model=RetrievalResponse,
    summary="Поиск по проиндексированным документам курса",
)
def retrieve_course_documents(
    course_id: int,
    q: str = Query(..., min_length=1, max_length=2000),
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    vector_store: VectorStore = Depends(get_vector_store),
):
    return RetrievalService.search_course(
        db,
        course_id=course_id,
        owner_id=current_user.id,
        query=q,
        limit=limit,
        vector_store=vector_store,
    )
