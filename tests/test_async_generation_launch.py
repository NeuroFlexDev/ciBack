from app.core.security import create_access_token, hash_password
from app.models.course_generation_settings import CourseGenerationSettings
from app.models.document import Document
from app.models.generation_run import GenerationRun
from app.models.document import DocumentChunk
from app.models.user import User
from tests.factories import make_course


def _settings():
    return {
        "title": "Async course", "goal": "Teach safely", "target_audience": None,
        "difficulty": "basic", "language": "en", "lesson_count": 1,
        "module_tests_enabled": False, "final_test_enabled": False,
    }


def _document(db, course, owner, *, status="indexed"):
    item = Document(storage_key="doc.txt", owner_id=owner.id, course_id=course.id,
                    version=1, status=status, content_hash="hash", source_type="upload",
                    original_filename="doc.txt", mime_type="text/plain", size_bytes=4)
    db.add(item); db.commit(); db.refresh(item)
    return item


def test_launch_returns_202_and_persists_before_enqueue(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    observed = {}
    def fake_enqueue(run_id):
        observed["run"] = db_session.get(GenerationRun, run_id)
        assert observed["run"].status == "queued"
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", fake_enqueue)
    response = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers,
                           json={"settings": _settings(), "document_ids": [document.id]})
    assert response.status_code == 202
    body = response.json(); assert body["run_id"] == observed["run"].id
    assert body["status_url"] == f"/api/generation-runs/{body['run_id']}"
    assert observed["run"].settings_snapshot["goal"] == "Teach safely"
    assert observed["run"].input_documents_snapshot == [{"document_id": document.id, "version": 1, "content_hash": "hash"}]


def test_launch_rejects_foreign_or_unready_document(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    foreign = User(email="foreign-launch@example.com", password_hash=hash_password("password123"))
    db_session.add(foreign); db_session.commit()
    foreign_course = make_course(db_session, owner_id=foreign.id)
    foreign_doc = _document(db_session, foreign_course, foreign)
    response = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers,
                           json={"settings": _settings(), "document_ids": [foreign_doc.id]})
    assert response.status_code == 422
    assert db_session.query(CourseGenerationSettings).filter_by(course_id=course.id).count() == 0
    unready = _document(db_session, course, auth_user, status="processing")
    response = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers,
                           json={"settings": _settings(), "document_ids": [unready.id]})
    assert response.status_code == 422


def test_second_active_run_is_conflict(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", lambda run_id: None)
    payload = {"settings": _settings(), "document_ids": [document.id]}
    assert client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload).status_code == 202
    second = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload)
    assert second.status_code == 409
    assert second.json()["detail"]["run_id"]


def test_queue_failure_marks_run_failed(client, db_session, auth_user, auth_headers, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", lambda run_id: (_ for _ in ()).throw(ConnectionError()))
    response = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers,
                           json={"settings": _settings(), "document_ids": [document.id]})
    assert response.status_code == 503
    run = db_session.query(GenerationRun).filter_by(course_id=course.id).one()
    assert (run.status, run.error_code, run.retryable) == ("failed", "queue_unavailable", True)


def test_worker_executes_prepared_run(db_session, auth_user, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    document.chunks.append(DocumentChunk(document_id=document.id, document_version=1, text="Context", chunk_index=0, metadata_json={}))
    db_session.add(CourseGenerationSettings(course_id=course.id, goal="Goal", difficulty="basic", language="en", lesson_count=1, module_tests_enabled=False, final_test_enabled=False, created_by=auth_user.id, updated_by=auth_user.id))
    course.status = "configured"; db_session.commit()
    run = __import__("app.services.pipeline_service", fromlist=["PipelineService"]).PipelineService.prepare_graph_generation(
        db_session, course_id=course.id, owner_id=auth_user.id, document_ids=[document.id]
    )
    monkeypatch.setattr("app.workers.generation.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.services.pipeline_service.generate_from_prompt", lambda *a, **k: {
        "nodes": [{"id": "m", "type": "module"}, {"id": "l", "type": "lesson"}],
        "edges": [{"source": "m", "target": "l"}],
    })
    monkeypatch.setattr(db_session, "close", lambda: None)
    from app.workers.generation import execute_generation_run
    execute_generation_run(run.id)
    db_session.refresh(run)
    assert run.status == "completed"
    assert run.output["graph_id"]
