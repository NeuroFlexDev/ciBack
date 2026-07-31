import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.database.db import Base
from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.document import Document, DocumentChunk
from app.models.generation_run import GenerationRun
from app.models.user import User
from app.services.pipeline_service import PipelineRunFailed, PipelineService


class MemoryStorage:
    def __init__(self, files):
        self.files = files

    def read_bytes(self, storage_key):
        return self.files[storage_key]


def _course_and_document(db_session, auth_user, content=b"# Topic\nSome useful text"):
    course = Course(name="Pipeline course", owner_id=auth_user.id)
    db_session.add(course)
    db_session.flush()
    document = Document(
        storage_key="document.txt",
        owner_id=auth_user.id,
        course_id=course.id,
        version=1,
        status="uploaded",
        content_hash="abc123",
        source_type="upload",
        original_filename="document.txt",
        mime_type="text/plain",
        size_bytes=len(content),
    )
    db_session.add(document)
    db_session.commit()
    return course, document, MemoryStorage({"document.txt": content})


def _fake_embeddings(document_id, version, chunks):
    return [
        f"document:{document_id}:v{version}:chunk:{chunk['chunk_index']}"
        for chunk in chunks
    ]


def test_reindex_is_idempotent_and_persists_run(
    db_session, auth_user, monkeypatch
):
    course, document, storage = _course_and_document(db_session, auth_user)
    monkeypatch.setattr(
        "app.services.pipeline_service.replace_document_embeddings",
        _fake_embeddings,
    )

    first = PipelineService.reindex_document(
        db_session,
        document_id=document.id,
        owner_id=auth_user.id,
        storage=storage,
    )
    first_count = (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document.id)
        .count()
    )
    second = PipelineService.reindex_document(
        db_session,
        document_id=document.id,
        owner_id=auth_user.id,
        storage=storage,
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert first_count > 0
    assert (
        db_session.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document.id)
        .count()
        == first_count
    )
    assert all(chunk.embedding_id for chunk in document.chunks)
    assert document.chunks[0].metadata_json == {
        "page": None,
        "section": "Topic",
        "document_id": document.id,
        "document_version": 1,
        "source": "document.txt",
        "source_type": "upload",
        "owner_id": auth_user.id,
        "organization_id": None,
        "course_id": course.id,
    }
    assert document.status == "indexed"


def test_reindex_failure_is_persisted(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'failure.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    db_session = sessionmaker(bind=engine)()
    auth_user = User(
        email="failure@example.com", password_hash=hash_password("password123")
    )
    db_session.add(auth_user)
    db_session.commit()
    _, document, storage = _course_and_document(db_session, auth_user, b"\n\n")
    monkeypatch.setattr(
        "app.services.pipeline_service.replace_document_embeddings",
        _fake_embeddings,
    )

    with pytest.raises(PipelineRunFailed) as raised:
        PipelineService.reindex_document(
            db_session,
            document_id=document.id,
            owner_id=auth_user.id,
            storage=storage,
        )

    run = db_session.get(GenerationRun, raised.value.run_id)
    db_session.refresh(document)
    assert raised.value.status_code == 422
    assert run.status == "failed"
    assert run.error
    assert document.status == "failed"
    assert document.processing_error == run.error
    db_session.close()


def test_graph_generation_reuses_fingerprint_and_force_creates_version(
    db_session, auth_user, monkeypatch
):
    course, document, _ = _course_and_document(db_session, auth_user)
    document.status = "indexed"
    document.chunks.append(
        DocumentChunk(
            document_id=document.id,
            document_version=document.version,
            text="Учебный модуль и его урок",
            chunk_index=0,
            metadata_json={},
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.pipeline_service.generate_from_prompt",
        lambda *args, **kwargs: {
            "nodes": [
                {"id": "module-1", "label": "Модуль", "type": "module"},
                {"id": "lesson-1", "label": "Урок", "type": "lesson"},
            ],
            "edges": [
                {
                    "source": "module-1",
                    "target": "lesson-1",
                    "relation": "contains",
                }
            ],
        },
    )

    first = PipelineService.generate_graph(
        db_session, course_id=course.id, owner_id=auth_user.id, force=False
    )
    reused = PipelineService.generate_graph(
        db_session, course_id=course.id, owner_id=auth_user.id, force=False
    )
    forced = PipelineService.generate_graph(
        db_session, course_id=course.id, owner_id=auth_user.id, force=True
    )

    graphs = (
        db_session.query(CourseGraph)
        .filter(CourseGraph.course_id == course.id)
        .order_by(CourseGraph.version)
        .all()
    )
    db_session.refresh(course)
    assert reused.id == first.id
    assert forced.id != first.id
    assert [graph.version for graph in graphs] == [1, 2]
    assert graphs[0].status == "archived"
    assert graphs[1].status == "draft"
    assert course.current_graph_id == graphs[1].id
    assert forced.output["node_count"] == 2


def test_invalid_generated_graph_persists_failed_run_without_partial_graph(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite:///{(tmp_path / 'graph-failure.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    db_session = sessionmaker(bind=engine)()
    user = User(
        email="graph-failure@example.com",
        password_hash=hash_password("password123"),
    )
    db_session.add(user)
    db_session.commit()
    course, document, _ = _course_and_document(db_session, user)
    document.status = "indexed"
    document.chunks.append(
        DocumentChunk(
            document_id=document.id,
            document_version=document.version,
            text="Контекст",
            chunk_index=0,
            metadata_json={},
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.pipeline_service.generate_from_prompt",
        lambda *args, **kwargs: {
            "nodes": [{"id": "known"}],
            "edges": [{"source": "known", "target": "missing"}],
        },
    )

    with pytest.raises(PipelineRunFailed) as raised:
        PipelineService.generate_graph(
            db_session, course_id=course.id, owner_id=user.id, force=True
        )

    run = db_session.get(GenerationRun, raised.value.run_id)
    assert run.status == "failed"
    assert run.error
    assert db_session.query(CourseGraph).count() == 0
    db_session.close()


def test_pipeline_api_hides_foreign_resources(
    client, db_session, auth_headers
):
    foreign = User(
        email="foreign-pipeline@example.com",
        password_hash=hash_password("password123"),
    )
    db_session.add(foreign)
    db_session.flush()
    course = Course(name="Foreign", owner_id=foreign.id)
    db_session.add(course)
    db_session.flush()
    document = Document(
        storage_key="foreign.txt",
        owner_id=foreign.id,
        course_id=course.id,
        version=1,
        status="uploaded",
        content_hash="foreign",
        source_type="upload",
        original_filename="foreign.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    run = GenerationRun(
        owner_id=foreign.id,
        course_id=course.id,
        document_id=document.id,
        run_type="document_index",
        status="queued",
        input_docs=[],
    )
    db_session.add_all([document, run])
    db_session.commit()

    assert (
        client.get(f"/api/documents/{document.id}", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/documents/{document.id}/reindex", headers=auth_headers
        ).status_code
        == 404
    )
