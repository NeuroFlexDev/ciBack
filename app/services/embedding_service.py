import logging

import faiss
import numpy as np

logger = logging.getLogger(__name__)
model = None

# Глобальный FAISS-индекс и метаданные (можно заменить на persistent storage)
index = faiss.IndexFlatL2(384)
metadata = []


def get_model():
    global model
    if model is not None:
        return model

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as exc:
        logger.warning("SentenceTransformer недоступен, семантический поиск отключён: %s", exc)
        model = False

    return model if model is not False else None

def embed_and_add(lesson_id: int, obj_type: str, text: str):
    active_model = get_model()
    if active_model is None or not text.strip():
        return
    embedding = active_model.encode([text])[0]
    index.add(np.array([embedding], dtype=np.float32))
    metadata.append({
        "lesson_id": lesson_id,
        "type": obj_type,
        "text": text
    })

def search(query: str, k: int = 5):
    active_model = get_model()
    if active_model is None or not metadata:
        return []

    query_vec = active_model.encode([query])[0]
    D, I = index.search(np.array([query_vec], dtype=np.float32), k)
    return [metadata[i] for i in I[0] if i < len(metadata)]
