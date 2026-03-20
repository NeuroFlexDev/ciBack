import io

from app.services import embedding_service
from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course


class DummyEmbeddingModel:
    def encode(self, texts):
        vectors = []
        for text in texts:
            base = float(len(text.split()) or 1)
            vectors.append([base, base / 2.0, 1.0])
        return vectors


def test_upload_indexes_document_and_search_returns_course_results(client, db_session, monkeypatch):
    embedding_service.clear_index()
    monkeypatch.setattr(embedding_service, "model", DummyEmbeddingModel())

    user, token = register_and_login(
        client,
        db_session,
        email="rag@example.com",
        password="secret123",
        full_name="RAG Owner",
    )
    course = make_course(
        db_session,
        name="RAG course",
        description="before",
        level=1,
        language=1,
        owner_id=user.id,
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.upload_service.generate_from_prompt",
        lambda *args, **kwargs: {"summary": "Updated from document"},
    )

    upload_response = client.post(
        f"/api/courses/{course.id}/upload-description",
        files={"file": ("course.txt", io.BytesIO(b"API contracts and semantic search for courses"), "text/plain")},
        headers=auth_headers(token),
    )
    assert upload_response.status_code == 200
    body = upload_response.json()
    assert body["summary"] == "Updated from document"
    assert body["indexed_chunks"] >= 1

    search_response = client.get(
        "/api/search",
        params={"q": "semantic search", "course_id": course.id},
    )
    assert search_response.status_code == 200
    results = search_response.json()["results"]
    assert results
    assert all(item["course_id"] == course.id for item in results)
    assert any("semantic search" in item["text"].lower() for item in results)

    embedding_service.clear_index()
