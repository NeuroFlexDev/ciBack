from fastapi import APIRouter, Query

from app.schemas.search import SearchResponse
from app.services.embedding_service import search

router = APIRouter()


@router.get("/search", summary="Семантический поиск по контенту курса", response_model=SearchResponse)
def semantic_search(
    q: str = Query(..., description="Поисковый запрос"),
    course_id: int | None = Query(None, description="Ограничить поиск конкретным course_id"),
    k: int = Query(5, ge=1, le=20),
):
    return SearchResponse(results=search(q, k=k, course_id=course_id))
