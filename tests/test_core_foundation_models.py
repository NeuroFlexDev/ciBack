import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.document import Document, DocumentChunk
from app.models.domain_enums import CourseGraphStatus, DocumentStatus
from app.schemas.course_graph import CourseGraphCreate, CourseGraphUpdate
from app.schemas.document import DocumentChunkCreate, DocumentCreate


def _course(db_session, auth_user) -> Course:
    course = Course(name="Foundation course", owner_id=auth_user.id)
    db_session.add(course)
    db_session.flush()
    return course


def _document(db_session, auth_user, course) -> Document:
    document = Document(
        storage_key=f"courses/{course.id}/source.pdf",
        owner_id=auth_user.id,
        course_id=course.id,
        version=1,
        status=DocumentStatus.UPLOADED.value,
        content_hash="a" * 64,
        source_type="upload",
        original_filename="source.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
    )
    db_session.add(document)
    db_session.flush()
    return document


def test_document_chunk_and_current_graph_round_trip(db_session, auth_user):
    course = _course(db_session, auth_user)
    document = _document(db_session, auth_user, course)
    chunk = DocumentChunk(
        document_id=document.id,
        document_version=document.version,
        text="Chunk text",
        page=1,
        section="Introduction",
        metadata_json={"source": "source.pdf"},
        chunk_index=0,
    )
    graph = CourseGraph(
        course_id=course.id,
        version=1,
        nodes=[{"id": "module_1", "type": "module"}],
        edges=[],
        created_by=auth_user.id,
        status=CourseGraphStatus.PUBLISHED.value,
    )
    db_session.add_all([chunk, graph])
    db_session.flush()
    course.current_graph = graph
    db_session.commit()

    db_session.expire_all()
    stored_course = db_session.get(Course, course.id)
    stored_document = db_session.get(Document, document.id)

    assert stored_course.current_graph.version == 1
    assert stored_course.current_graph.nodes[0]["id"] == "module_1"
    assert stored_document.chunks[0].metadata_json == {"source": "source.pdf"}
    assert stored_document.chunks[0].document_version == stored_document.version


def test_duplicate_chunk_position_is_rejected(db_session, auth_user):
    course = _course(db_session, auth_user)
    document = _document(db_session, auth_user, course)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add_all(
                [
                    DocumentChunk(
                        document_id=document.id,
                        document_version=1,
                        text="First",
                        chunk_index=0,
                    ),
                    DocumentChunk(
                        document_id=document.id,
                        document_version=1,
                        text="Duplicate",
                        chunk_index=0,
                    ),
                ]
            )
            db_session.flush()


def test_foundation_schemas_validate_versions_and_mutability():
    document = DocumentCreate(
        storage_key="courses/1/source.txt",
        owner_id=1,
        course_id=1,
        content_hash="b" * 64,
        source_type="upload",
        original_filename="source.txt",
        mime_type="text/plain",
        size_bytes=0,
    )
    graph = CourseGraphCreate(
        course_id=1,
        version=1,
        nodes=[{"id": "lesson_1"}],
        edges=[],
        created_by=1,
    )

    assert document.status is DocumentStatus.UPLOADED
    assert graph.status is CourseGraphStatus.DRAFT
    assert set(CourseGraphUpdate.model_fields) == {"status"}

    with pytest.raises(ValidationError):
        DocumentChunkCreate(
            document_id=1,
            document_version=1,
            text="chunk",
            page=0,
            chunk_index=0,
        )

    with pytest.raises(ValidationError):
        CourseGraphCreate(
            course_id=1,
            version=0,
            created_by=1,
        )
