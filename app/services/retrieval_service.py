from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repositories.retrieval import RetrievalRepository
from app.schemas.retrieval import RetrievalCitation, RetrievalResponse
from app.services.vector_store import VectorSearchFilters, VectorStore


class RetrievalService:
    @staticmethod
    def search_course(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        query: str,
        limit: int,
        vector_store: VectorStore,
    ) -> RetrievalResponse:
        if RetrievalRepository.get_owned_course(db, course_id, owner_id) is None:
            raise HTTPException(status_code=404, detail="Курс не найден")

        chunks = RetrievalRepository.accessible_chunks(db, course_id, owner_id)
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        try:
            matches = vector_store.search(
                query,
                VectorSearchFilters(
                    owner_id=owner_id,
                    course_id=course_id,
                    allowed_chunk_ids=frozenset(chunks_by_id),
                ),
                limit,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503, detail="Семантический поиск временно недоступен"
            ) from exc

        citations = []
        for match in matches:
            chunk_id = match.metadata.get("chunk_id")
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                # Defense in depth: a backend must never widen DB-derived ACL.
                continue
            document = chunk.document
            citations.append(
                RetrievalCitation(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_version=chunk.document_version,
                    source_document=document.original_filename,
                    source_type=document.source_type,
                    page=chunk.page,
                    section=chunk.section,
                    text=chunk.text,
                    score=match.score,
                )
            )
            if len(citations) == limit:
                break

        return RetrievalResponse(
            query=query, course_id=course_id, citations=citations
        )
