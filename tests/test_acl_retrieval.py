import numpy as np

from main import app
from app.models.course import Course
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.embedding_service import get_vector_store
from app.services.vector_store import (
    FaissVectorStore,
    VectorMatch,
    VectorRecord,
)


class FakeVectorStore:
    def __init__(self, matches):
        self.matches = matches
        self.filters = None

    def replace_document(self, document_id, document_version, records):
        return [record.embedding_id for record in records]

    def delete_document(self, document_id):
        return None

    def search(self, query, filters, limit):
        self.filters = filters
        return self.matches[:limit]


def _document(db_session, course, owner_id, filename):
    document = Document(
        storage_key=filename,
        owner_id=owner_id,
        course_id=course.id,
        version=1,
        status="indexed",
        content_hash=f"hash-{filename}",
        source_type="upload",
        original_filename=filename,
        mime_type="text/plain",
        size_bytes=10,
    )
    db_session.add(document)
    db_session.flush()
    chunk = DocumentChunk(
        document_id=document.id,
        document_version=1,
        text=f"Text from {filename}",
        page=2,
        section="Scope",
        metadata_json={"source": filename},
        chunk_index=0,
        embedding_id=f"document:{document.id}:v1:chunk:0",
    )
    db_session.add(chunk)
    db_session.flush()
    return document, chunk


def test_course_retrieval_prefilters_acl_and_returns_db_citations(
    client, db_session, auth_user, auth_headers
):
    owned_course = Course(name="Owned", owner_id=auth_user.id)
    foreign_user = User(email="foreign-retrieval@example.com", password_hash="hash")
    db_session.add_all([owned_course, foreign_user])
    db_session.flush()
    foreign_course = Course(name="Foreign", owner_id=foreign_user.id)
    db_session.add(foreign_course)
    db_session.flush()
    _, owned_chunk = _document(db_session, owned_course, auth_user.id, "owned.txt")
    _, foreign_chunk = _document(
        db_session, foreign_course, foreign_user.id, "foreign.txt"
    )
    db_session.commit()

    store = FakeVectorStore(
        [
            VectorMatch(
                embedding_id=foreign_chunk.embedding_id,
                text="untrusted foreign text",
                score=1.0,
                metadata={"chunk_id": foreign_chunk.id},
            ),
            VectorMatch(
                embedding_id=owned_chunk.embedding_id,
                text="untrusted owned text",
                score=0.8,
                metadata={"chunk_id": owned_chunk.id},
            ),
        ]
    )
    app.dependency_overrides[get_vector_store] = lambda: store
    try:
        response = client.get(
            f"/api/courses/{owned_course.id}/retrieval",
            params={"q": "scope", "limit": 5},
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_vector_store, None)

    assert response.status_code == 200
    assert store.filters.owner_id == auth_user.id
    assert store.filters.course_id == owned_course.id
    assert store.filters.allowed_chunk_ids == frozenset({owned_chunk.id})
    assert response.json()["citations"] == [
        {
            "chunk_id": owned_chunk.id,
            "document_id": owned_chunk.document_id,
            "document_version": 1,
            "source_document": "owned.txt",
            "source_type": "upload",
            "page": 2,
            "section": "Scope",
            "text": "Text from owned.txt",
            "score": 0.8,
        }
    ]


def test_course_retrieval_hides_foreign_course(client, db_session, auth_headers):
    foreign_course = Course(name="Foreign", owner_id=None)
    db_session.add(foreign_course)
    db_session.commit()

    response = client.get(
        f"/api/courses/{foreign_course.id}/retrieval",
        params={"q": "hidden"},
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_faiss_store_prefilters_and_replaces_old_document_version():
    class DummyModel:
        def encode(self, texts):
            return np.asarray(
                [[float(len(text)), 0.0] for text in texts], dtype=np.float32
            )

    store = FaissVectorStore(lambda: DummyModel())
    base_metadata = {
        "document_id": 10,
        "owner_id": 1,
        "course_id": 20,
        "chunk_id": 100,
    }
    store.replace_document(
        10,
        1,
        [VectorRecord("old", "old", {**base_metadata, "document_version": 1})],
    )
    store.replace_document(
        10,
        2,
        [VectorRecord("new", "new text", {**base_metadata, "document_version": 2})],
    )
    store.replace_document(
        11,
        1,
        [
            VectorRecord(
                "foreign",
                "query",
                {
                    "chunk_id": 101,
                    "owner_id": 2,
                    "course_id": 20,
                },
            )
        ],
    )

    from app.services.vector_store import VectorSearchFilters

    matches = store.search(
        "query",
        VectorSearchFilters(
            owner_id=1, course_id=20, allowed_chunk_ids=frozenset({100, 101})
        ),
        5,
    )

    assert [match.embedding_id for match in matches] == ["new"]
