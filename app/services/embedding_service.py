import logging
from threading import RLock

import faiss
import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)
model = None
_index_lock = RLock()

# Глобальный FAISS-индекс и метаданные (можно заменить на persistent storage)
index = faiss.IndexFlatL2(384)
metadata = []


def get_model():
    global model
    if model is not None:
        return model

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(settings.EMBEDDING_MODEL)
    except Exception as exc:
        logger.warning("SentenceTransformer недоступен, семантический поиск отключён: %s", exc)
        model = False

    return model if model is not False else None

def embed_and_add(lesson_id: int, obj_type: str, text: str):
    active_model = get_model()
    if active_model is None or not text.strip():
        return
    embedding = active_model.encode([text])[0]
    with _index_lock:
        index.add(np.array([embedding], dtype=np.float32))
        metadata.append({
            "kind": "lesson",
            "lesson_id": lesson_id,
            "type": obj_type,
            "text": text
        })


def replace_document_embeddings(
    document_id: int,
    document_version: int,
    chunks: list[dict],
) -> list[str]:
    """Replace one document version in the demo FAISS index without duplicates."""
    active_model = get_model()
    if active_model is None:
        raise RuntimeError("Embedding model is unavailable")

    retained = [
        item
        for item in metadata
        if not (
            item.get("kind") == "document"
            and item.get("document_id") == document_id
            and item.get("document_version") == document_version
        )
    ]
    document_items = []
    embedding_ids = []
    for chunk in chunks:
        embedding_id = (
            f"document:{document_id}:v{document_version}:chunk:{chunk['chunk_index']}"
        )
        embedding_ids.append(embedding_id)
        document_items.append(
            {
                "kind": "document",
                "document_id": document_id,
                "document_version": document_version,
                "embedding_id": embedding_id,
                "page": chunk.get("page"),
                "section": chunk.get("section"),
                "text": chunk["text"],
            }
        )

    rebuilt = retained + document_items
    texts = [item["text"] for item in rebuilt]
    vectors = (
        np.asarray(active_model.encode(texts), dtype=np.float32)
        if texts
        else np.empty((0, 384), dtype=np.float32)
    )
    with _index_lock:
        index.reset()
        metadata[:] = rebuilt
        if len(vectors):
            index.add(vectors)
    return embedding_ids

def search(query: str, k: int = 5, allowed_lesson_ids: set[int] | None = None):
    active_model = get_model()
    if active_model is None or not metadata or allowed_lesson_ids == set():
        return []

    query_vec = active_model.encode([query])[0]
    with _index_lock:
        candidate_count = len(metadata) if allowed_lesson_ids is not None else k
        _, indices = index.search(
            np.array([query_vec], dtype=np.float32),
            min(candidate_count, len(metadata)),
        )
        results = []
        for item_index in indices[0]:
            if item_index < 0 or item_index >= len(metadata):
                continue
            item = metadata[item_index]
            if not isinstance(item, dict):
                if allowed_lesson_ids is None:
                    results.append(item)
                if len(results) == k:
                    break
                continue
            if item.get("kind", "lesson") != "lesson":
                continue
            if (
                allowed_lesson_ids is not None
                and item.get("lesson_id") not in allowed_lesson_ids
            ):
                continue
            results.append(item)
            if len(results) == k:
                break
    return results
