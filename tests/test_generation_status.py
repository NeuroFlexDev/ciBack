from datetime import datetime

import pytest

from app.core.security import hash_password
from app.models.document import Document
from app.models.generation_run import GenerationRun
from app.models.user import User
from tests.factories import make_course


def _run(db, owner, course, **overrides):
    values = {
        "owner_id": owner.id,
        "course_id": course.id,
        "run_type": "graph_generation",
        "status": "queued",
        "current_stage": "queued",
        "progress_percent": 0,
        "input_docs": [],
        "input_documents_snapshot": [],
        "settings_snapshot": {},
        "attempt": 1,
        "queued_at": datetime.utcnow(),
    }
    values.update(overrides)
    item = GenerationRun(**values)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.mark.parametrize(
    ("status", "stage", "progress", "expected"),
    [
        ("queued", "queued", 0, ["pending", "pending", "pending"]),
        ("running", "knowledge_extraction", 10, ["running", "pending", "pending"]),
        ("running", "structure_building", 40, ["completed", "running", "pending"]),
        ("running", "lesson_writing", 80, ["completed", "completed", "running"]),
        ("completed", "completed", 100, ["completed", "completed", "completed"]),
    ],
)
def test_generation_status_contract(
    client, db_session, auth_user, auth_headers, status, stage, progress, expected
):
    course = make_course(db_session, owner_id=auth_user.id)
    run = _run(
        db_session, auth_user, course, status=status, current_stage=stage,
        progress_percent=progress,
    )

    response = client.get(f"/api/generation-runs/{run.id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == body["run_id"] == run.id
    assert body["progress_percent"] == progress
    assert [item["code"] for item in body["stages"]] == [
        "knowledge_extraction", "structure_building", "lesson_writing"
    ]
    assert [item["status"] for item in body["stages"]] == expected
    assert body["status_error"] is None


def test_failed_status_exposes_only_safe_error(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    run = _run(
        db_session, auth_user, course, status="failed",
        current_stage="structure_building", progress_percent=40,
        error="internal traceback text", error_code="llm_timeout",
        error_message="Не удалось завершить генерацию курса.", retryable=True,
        finished_at=datetime.utcnow(),
    )

    body = client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).json()

    assert body["status_error"] == {
        "code": "llm_timeout",
        "message": "Не удалось завершить генерацию курса.",
        "retryable": True,
    }
    assert "traceback" not in body["status_error"]["message"]
    assert body["error"] == "Не удалось завершить генерацию курса."


def test_polling_is_persisted_and_does_not_create_run(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    run = _run(db_session, auth_user, course, status="running", current_stage="lesson_writing", progress_percent=80)
    count = db_session.query(GenerationRun).count()
    db_session.expire_all()

    assert client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).status_code == 200
    assert client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).status_code == 200
    assert db_session.query(GenerationRun).count() == count


def test_foreign_and_unknown_status_are_404(client, db_session, auth_user, auth_headers):
    foreign = User(email="status-foreign@example.com", password_hash=hash_password("password123"))
    db_session.add(foreign); db_session.commit()
    course = make_course(db_session, owner_id=foreign.id)
    run = _run(db_session, foreign, course)

    assert client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).status_code == 404
    assert client.get("/api/generation-runs/999999", headers=auth_headers).status_code == 404


def test_retry_creates_new_run_from_immutable_snapshot(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    document = Document(
        storage_key="retry.txt", owner_id=auth_user.id, course_id=course.id,
        version=2, status="indexed", content_hash="retry-hash", source_type="upload",
        original_filename="retry.txt", mime_type="text/plain", size_bytes=5,
    )
    db_session.add(document); db_session.commit(); db_session.refresh(document)
    snapshot = [{"document_id": document.id, "version": 2, "content_hash": "retry-hash"}]
    original = _run(
        db_session, auth_user, course, status="failed", current_stage="structure_building",
        progress_percent=40, retryable=True, error_code="llm_timeout",
        error_message="Safe error", input_docs=snapshot,
        input_documents_snapshot=snapshot, settings_snapshot={"lesson_count": 3},
    )
    queued = []
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", queued.append)

    response = client.post(f"/api/generation-runs/{original.id}/retry", headers=auth_headers)

    assert response.status_code == 202
    retry = db_session.get(GenerationRun, response.json()["run_id"])
    assert queued == [retry.id]
    assert retry.id != original.id
    assert retry.retry_of_run_id == original.id
    assert retry.attempt == 2
    assert retry.settings_snapshot == original.settings_snapshot
    assert retry.input_documents_snapshot == snapshot
    assert original.status == "failed"


def test_retry_rejects_nonretryable_and_changed_document(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    run = _run(db_session, auth_user, course, status="failed", retryable=False)
    response = client.post(f"/api/generation-runs/{run.id}/retry", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "generation_run_not_retryable"

    run.retryable = True
    run.input_documents_snapshot = [{"document_id": 99999, "version": 1, "content_hash": "gone"}]
    db_session.commit()
    response = client.post(f"/api/generation-runs/{run.id}/retry", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "retry_input_unavailable"


def test_retry_rejects_when_course_has_active_run(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    original = _run(db_session, auth_user, course, status="failed", retryable=True)
    _run(db_session, auth_user, course, status="queued")

    response = client.post(f"/api/generation-runs/{original.id}/retry", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "generation_run_active"


def test_retry_queue_failure_persists_new_failed_attempt(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    document = Document(
        storage_key="queue.txt", owner_id=auth_user.id, course_id=course.id,
        version=1, status="indexed", content_hash="queue-hash", source_type="upload",
        original_filename="queue.txt", mime_type="text/plain", size_bytes=5,
    )
    db_session.add(document); db_session.commit(); db_session.refresh(document)
    snapshot = [{"document_id": document.id, "version": 1, "content_hash": "queue-hash"}]
    original = _run(
        db_session, auth_user, course, status="failed", retryable=True,
        input_docs=snapshot, input_documents_snapshot=snapshot,
    )
    monkeypatch.setattr(
        "app.routes.pipeline.enqueue_generation",
        lambda run_id: (_ for _ in ()).throw(ConnectionError()),
    )

    response = client.post(f"/api/generation-runs/{original.id}/retry", headers=auth_headers)

    assert response.status_code == 503
    retry = db_session.query(GenerationRun).filter_by(retry_of_run_id=original.id).one()
    assert (retry.status, retry.error_code, retry.retryable) == (
        "failed", "queue_unavailable", True
    )
