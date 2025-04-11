# app/services/embedding_service.py
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

model = SentenceTransformer("all-MiniLM-L6-v2")

# Глобальный FAISS-индекс и метаданные (можно заменить на persistent storage)
index = faiss.IndexFlatL2(384)
metadata = []

def embed_and_add(lesson_id: int, obj_type: str, text: str):
    if not text.strip():
        return
    embedding = model.encode([text])[0]
    index.add(np.array([embedding], dtype=np.float32))
    metadata.append({
        "lesson_id": lesson_id,
        "type": obj_type,
        "text": text
    })

def search(query: str, k: int = 5):
    query_vec = model.encode([query])[0]
    D, I = index.search(np.array([query_vec], dtype=np.float32), k)
    return [metadata[i] for i in I[0] if i < len(metadata)]
