from app.models.document import Document
from app.services.file_storage import LocalFileStorage, get_file_storage
from app.services.pipeline_service import PipelineService
from main import app
from tests.factories import make_course


def _fake_embeddings(document_id, version, chunks):
    return [f"{document_id}:{version}:{item['chunk_index']}" for item in chunks]


def test_replacement_upload_switches_current_version_only_after_indexing(
    client,
    db_session,
    auth_user,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    storage = LocalFileStorage(tmp_path, 1024 * 1024)
    app.dependency_overrides[get_file_storage] = lambda: storage
    monkeypatch.setattr(
        "app.services.pipeline_service.replace_document_embeddings",
        _fake_embeddings,
    )
    course = make_course(db_session, owner_id=auth_user.id)

    first_response = client.post(
        f"/api/courses/{course.id}/documents",
        files={"file": ("policy.txt", b"old policy", "text/plain")},
        headers=auth_headers,
    )
    first_id = first_response.json()["id"]
    PipelineService.reindex_document(
        db_session,
        document_id=first_id,
        owner_id=auth_user.id,
        storage=storage,
    )

    second_response = client.post(
        f"/api/courses/{course.id}/documents",
        data={"replace_document_id": str(first_id)},
        files={"file": ("policy.txt", b"new policy", "text/plain")},
        headers=auth_headers,
    )
    assert second_response.status_code == 202
    second_id = second_response.json()["id"]
    first = db_session.get(Document, first_id)
    second = db_session.get(Document, second_id)
    assert second.document_key == first.document_key
    assert second.version == 2
    assert (first.is_current, second.is_current) == (True, False)

    PipelineService.reindex_document(
        db_session,
        document_id=second_id,
        owner_id=auth_user.id,
        storage=storage,
    )
    db_session.refresh(first)
    db_session.refresh(second)
    assert (first.is_current, second.is_current) == (False, True)

    listed = client.get(
        f"/api/courses/{course.id}/documents", headers=auth_headers
    ).json()
    assert [item["id"] for item in listed["items"]] == [second_id]
