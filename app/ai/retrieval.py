"""Persistent embeddings + lexical retrieval, always scoped by database ACL first."""
import hashlib
import logging
import math
from langchain_openai import OpenAIEmbeddings
from sqlalchemy.orm import Session
from app.ai import gateway
from app.ai import providers
from app.ai.evidence import rank_sources
from app.core.config import settings
from app.models.ai_state import ChunkEmbedding
from app.repositories.retrieval import RetrievalRepository
from app.services.vector_store import VectorMatch, VectorSearchFilters

logger = logging.getLogger(__name__)


def metered_embedding(db, owner_id, course_id, texts, *, query=False):
    from decimal import Decimal
    from app.services.ai_resources import reserve, finish
    provider = providers.registry().get(settings.EMBEDDING_PROVIDER)
    if not provider or not providers.available(provider):
        raise providers.AIUnavailable()
    if not provider.local and settings.EMBEDDING_RUB_PER_MILLION is None:
        raise ValueError("Embedding price is not configured; lexical retrieval retained")
    tokens = sum(gateway.token_count(text) for text in texts)
    rate = Decimal(0) if provider.local else settings.EMBEDDING_RUB_PER_MILLION
    resource = reserve(db, owner_id=owner_id, course_id=course_id, operation="embedding",
        lane="interactive" if query else "metered", tokens=tokens, cost=tokens * rate / Decimal(1000000), timeout=60)
    db.commit()
    try:
        client = embedding_client()
        result = client.embed_query(texts[0]) if query else client.embed_documents(texts)
        finish(db, resource)
        return result
    except Exception:
        finish(db, resource, failed=True)
        raise


def embedding_client():
    provider = providers.registry().get(settings.EMBEDDING_PROVIDER)
    if not provider or not providers.available(provider):
        raise providers.AIUnavailable()
    return OpenAIEmbeddings(model=settings.AI_MODEL_EMBEDDING,
        api_key=providers.credential(provider), base_url=str(provider.base_url),
        check_embedding_ctx_length=False, max_retries=0, request_timeout=10, chunk_size=16)


def persist_embeddings(db: Session, chunks: list) -> list[str]:
    existing = {row.chunk_id: row for row in db.query(ChunkEmbedding).filter(
        ChunkEmbedding.chunk_id.in_([chunk.id for chunk in chunks]),
        ChunkEmbedding.model == settings.AI_MODEL_EMBEDDING).all()}
    missing = [chunk for chunk in chunks if chunk.id not in existing or
               existing[chunk.id].text_hash != hashlib.sha256(chunk.text.encode()).hexdigest()]
    lexical_ids = [f"chunk:{chunk.id}" for chunk in chunks]
    if missing and settings.EMBEDDING_PROVIDER not in gateway.available_provider_ids():
        return lexical_ids
    try:
        vectors = metered_embedding(db, missing[0].document.owner_id, missing[0].document.course_id,
            [chunk.text for chunk in missing]) if missing else []
        if len(vectors) != len(missing) or any(
            not vector or not all(math.isfinite(x) for x in vector) for vector in vectors
        ):
            raise ValueError("Embedding provider returned an invalid batch")
    except Exception as exc:
        # The chunks already live in the DB. Lexical retrieval remains usable;
        # a later explicit reindex can fill missing vectors. Never catch DB errors.
        logger.warning("Semantic indexing unavailable; lexical index retained (%s)", type(exc).__name__)
        return lexical_ids
    for chunk, vector in zip(missing, vectors, strict=True):
        row = existing.get(chunk.id) or ChunkEmbedding(chunk_id=chunk.id, model=settings.AI_MODEL_EMBEDDING)
        row.text_hash = hashlib.sha256(chunk.text.encode()).hexdigest()
        row.vector = vector
        db.add(row)
    db.flush()
    return [f"db:{settings.AI_MODEL_EMBEDDING}:{chunk.id}" for chunk in chunks]


class PersistentVectorStore:
    def __init__(self, db: Session, *, local_only: bool = False):
        self.db = db
        self.local_only = local_only

    def search(self, query: str, filters: VectorSearchFilters, limit: int) -> list[VectorMatch]:
        if not filters.allowed_chunk_ids or not query.strip():
            return []
        chunks = [chunk for chunk in RetrievalRepository.accessible_chunks(self.db, filters.course_id, filters.owner_id)
                  if chunk.id in filters.allowed_chunk_ids]
        if not chunks:
            return []
        by_id = {chunk.id: chunk for chunk in chunks}
        lexical = rank_sources(query, [{"id": chunk.id, "quote": chunk.text} for chunk in chunks], limit=limit * 3)
        scores = {item["id"]: 1 / (60 + i) for i, item in enumerate(lexical, 1)}
        rows = self.db.query(ChunkEmbedding).filter(ChunkEmbedding.chunk_id.in_(by_id),
            ChunkEmbedding.model == settings.AI_MODEL_EMBEDDING).all()
        if rows and not self.local_only and settings.EMBEDDING_PROVIDER in gateway.available_provider_ids():
            try:
                vector = metered_embedding(self.db, filters.owner_id, filters.course_id, [query], query=True)
                norm = math.sqrt(sum(x*x for x in vector)) or 1
                candidates = []
                for row in rows:
                    if len(row.vector) != len(vector) or row.text_hash != hashlib.sha256(by_id[row.chunk_id].text.encode()).hexdigest():
                        continue
                    other_norm = math.sqrt(sum(x*x for x in row.vector)) or 1
                    similarity = sum(a*b for a,b in zip(vector,row.vector)) / (norm * other_norm)
                    candidates.append((similarity, row.chunk_id))
                for i, (_, chunk_id) in enumerate(sorted(candidates, reverse=True)[:limit*3], 1):
                    scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60+i)
            except Exception:
                # Database lexical retrieval stays available during embedding outages.
                pass
        return [VectorMatch(embedding_id=f"chunk:{chunk_id}", text=by_id[chunk_id].text,
                            score=score, metadata={"chunk_id": chunk_id})
                for chunk_id, score in sorted(scores.items(), key=lambda item: -item[1])[:limit]]
