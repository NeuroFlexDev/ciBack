from typing import Any

import faiss
import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"

model = None
index = None
metadata: list[dict[str, Any]] = []


def get_model():
    global model
    if model is None:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(MODEL_NAME)
    return model


def clear_index() -> None:
    global index, metadata
    index = None
    metadata = []


def split_text(text: str, chunk_size: int = 160, overlap: int = 30) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + chunk_size)
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end == len(words):
            break
        start = max(end - overlap, start + 1)
    return chunks


def ensure_index(vector_size: int):
    global index
    if index is None:
        index = faiss.IndexFlatL2(vector_size)
    return index


def add_embeddings(texts: list[str], items: list[dict[str, Any]]) -> int:
    if not texts:
        return 0

    current_model = get_model()
    vectors = current_model.encode(texts)
    array = np.array(vectors, dtype=np.float32)
    ensure_index(array.shape[1]).add(array)
    metadata.extend(items)
    return len(items)


def embed_and_add(lesson_id: int, obj_type: str, text: str):
    if not text.strip():
        return 0
    return add_embeddings(
        [text],
        [
            {
                "lesson_id": lesson_id,
                "type": obj_type,
                "text": text,
            }
        ],
    )


def index_course_document(
    course_id: int,
    text: str,
    *,
    source_name: str | None = None,
    content_type: str | None = None,
) -> int:
    chunks = split_text(text)
    items = [
        {
            "course_id": course_id,
            "type": "course_document",
            "text": chunk,
            "source_name": source_name,
            "content_type": content_type,
            "chunk_index": index,
        }
        for index, chunk in enumerate(chunks)
    ]
    return add_embeddings(chunks, items)


def search(query: str, k: int = 5, *, course_id: int | None = None):
    if not query.strip() or not metadata or index is None:
        return []

    current_model = get_model()
    query_vec = np.array(current_model.encode([query]), dtype=np.float32)

    fetch_k = min(max(k * 5, 20), len(metadata))
    distances, indexes = index.search(query_vec, fetch_k)

    results = []
    for distance, hit_index in zip(distances[0], indexes[0]):
        if hit_index < 0 or hit_index >= len(metadata):
            continue
        item = metadata[hit_index]
        if not isinstance(item, dict):
            if course_id is None:
                results.append(item)
            if len(results) == k:
                break
            continue
        if course_id is not None and item.get("course_id") != course_id:
            continue
        results.append({**item, "score": float(distance)})
        if len(results) == k:
            break
    return results
