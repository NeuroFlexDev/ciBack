# app/routes/search.py
from fastapi import APIRouter, Query

from app.services.embedding_service import search

router = APIRouter()


@router.get("/search", summary="Семантический поиск по контенту курса")
def semantic_search(q: str = Query(..., description="Поисковый запрос")):
    results = search(q)
    return {"results": results}
