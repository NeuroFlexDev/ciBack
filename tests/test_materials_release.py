from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED
import json
import pytest
from PIL import Image
from fastapi import HTTPException
from app.core.config import settings, AIProvider
from app.core.security import create_access_token, hash_password
from app.models.user import User
from app.models.document import Document, DocumentChunk
from app.models.generation_run import GenerationRun
from app.models.course_version import CourseVersion
from app.services.document_safety import validate_container
from app.services.generation_preflight import generation_preflight
from app.services.generation_control import check_cancelled, GenerationCancelled
from app.services import learner_assistant_service as assistant
from tests.factories import make_course


def png():
    output = BytesIO()
    Image.new("RGB", (20, 10), "blue").save(output, "PNG")
    return output.getvalue()


def test_image_upload_private_review_and_immutable_approval(client, db_session, auth_user, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    course = make_course(db_session, owner_id=auth_user.id)
    response = client.post(f"/api/courses/{course.id}/images", headers=auth_headers,
                           files={"file": ("image.png", png(), "image/png")})
    assert response.status_code == 201
    item = response.json()
    assert (item["status"], item["width"], item["height"]) == ("pending_review", 20, 10)
    assert client.get(item["content_url"]).status_code == 401
    assert client.get(item["content_url"], headers=auth_headers).content.startswith(b"\x89PNG")
    other = User(email="image-other@example.com", password_hash=hash_password("password123"))
    db_session.add(other); db_session.commit()
    foreign = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    assert client.get(item["content_url"], headers=foreign).status_code == 404
    assert client.post(f"/api/images/{item['id']}/review", json={"decision": "approved"}, headers=auth_headers).status_code == 200
    assert client.post(f"/api/images/{item['id']}/review", json={"decision": "rejected"}, headers=auth_headers).status_code == 409


def test_image_rejects_svg_and_pixels_before_storage(client, db_session, auth_user, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    course = make_course(db_session, owner_id=auth_user.id)
    response = client.post(f"/api/courses/{course.id}/images", headers=auth_headers,
        files={"file": ("fake.png", b'<svg onload="fetch(\'/secret\')"/>', "image/png")})
    assert response.status_code == 422
    monkeypatch.setattr(settings, "IMAGE_MAX_PIXELS", 100)
    assert client.post(f"/api/courses/{course.id}/images", headers=auth_headers,
                      files={"file": ("large.png", png(), "image/png")}).status_code == 413
    assert not list(tmp_path.rglob("*"))


def test_image_generation_idempotency_and_quota(client, db_session, auth_user, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDERS", [AIProvider(id="images", base_url="http://localhost:9999/v1", local=True)])
    monkeypatch.setattr(settings, "IMAGE_PROVIDER", "images")
    monkeypatch.setattr(settings, "IMAGE_MODEL", "configured-image-model")
    monkeypatch.setattr(settings, "IMAGE_MONTHLY_LIMIT", 1)
    queued = []
    monkeypatch.setattr("app.services.image_service.enqueue_image", queued.append)
    course = make_course(db_session, owner_id=auth_user.id)
    path = f"/api/courses/{course.id}/images/generate"
    payload = {"prompt": "Diagram of a cell", "request_id": "same-request-123"}
    first = client.post(path, headers=auth_headers, json=payload)
    second = client.post(path, headers=auth_headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert queued == [first.json()["id"]]
    assert client.post(path, headers=auth_headers, json={**payload, "prompt": "Different"}).status_code == 409
    assert client.post(path, headers=auth_headers, json={**payload, "request_id": "second-request"}).status_code == 429


def test_docx_zip_bomb_is_rejected_before_parser(monkeypatch):
    content = BytesIO()
    with ZipFile(content, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 2_000_000)
    with pytest.raises(ValueError, match="сжатия"):
        validate_container(content.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_docx_math_is_not_silently_dropped():
    from docx import Document as DocxDocument
    from docx.oxml import parse_xml
    from app.services.document_processing import extract_blocks
    document = DocxDocument()
    paragraph = document.add_paragraph("Fraction: ")
    paragraph._p.append(parse_xml('<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath>'))
    output = BytesIO(); document.save(output)
    blocks = extract_blocks(output.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert blocks[0].text == r"Fraction: $\frac{a}{b}$"


def test_hundred_lesson_preflight_rejects_impossible_budget(db_session, auth_user):
    document = SimpleNamespace(chunks=[SimpleNamespace(text="Source " * 200, is_deleted=False, id=1,
        document_id=1, document_version=1, chunk_index=0, page=1, section=None, metadata_json={})],
        id=1, version=1, content_hash="x", size_bytes=1400, original_filename="source.txt", source_type="upload")
    result = generation_preflight([document], {"lesson_count": 100})
    assert result["minimum_calls"] >= 203
    assert not result["allowed"]
    assert result["reasons"]


def test_cancelled_run_stops_before_next_paid_call(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    run = GenerationRun(owner_id=auth_user.id, course_id=course.id, run_type="graph_generation", status="running")
    db_session.add(run); db_session.commit()
    result = client.post(f"/api/generation-runs/{run.id}/cancel", headers=auth_headers)
    assert result.status_code == 200
    assert "Останавливаем" in result.json()["status_notice"]
    with pytest.raises(GenerationCancelled):
        check_cancelled(db_session, run.id)


def test_generation_request_id_recovers_lost_response_without_new_job(client, db_session, auth_user, auth_headers, monkeypatch):
    from tests.test_async_generation_launch import _settings, _document
    course = make_course(db_session, owner_id=auth_user.id)
    document = _document(db_session, course, auth_user)
    queued = []
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", queued.append)
    payload = {"settings": _settings(), "document_ids": [document.id], "request_id": "retry-same-request"}
    first = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload)
    second = client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert queued == [first.json()["run_id"]]
    payload["settings"]["goal"] = "Changed after clicking"
    assert client.post(f"/api/courses/{course.id}/generation-runs", headers=auth_headers, json=payload).status_code == 409


def test_course_metadata_draft_nullable_fields_and_conflict(client, db_session, auth_user, auth_headers):
    draft = client.post("/api/courses/drafts", headers=auth_headers).json()
    course_id = draft["id"]
    response = client.put(f"/api/courses/{course_id}", headers=auth_headers,
                          json={"title": "Named draft", "expected_revision": 1})
    assert response.status_code == 200
    assert response.json()["description"] == ""
    revision = response.json()["content_revision"]
    assert revision > 1
    stale = client.put(f"/api/courses/{course_id}", headers=auth_headers,
                       json={"title": "Must not overwrite", "expected_revision": 1})
    assert stale.status_code == 409
    from app.models.course import Course
    db_session.expire_all()
    assert db_session.get(Course, course_id).name == "Named draft"
    assert client.get(f"/api/courses/{course_id}/structure", headers=auth_headers).json()["content_revision"] == revision


def test_local_only_excludes_remote_even_with_key(monkeypatch):
    from app.ai.providers import configured
    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", True)
    monkeypatch.setenv("REMOTE_TEST_KEY", "never-send-this")
    assert not configured(AIProvider(id="remote", base_url="https://provider.invalid/v1", api_key_env="REMOTE_TEST_KEY"))
    assert configured(AIProvider(id="local", base_url="http://localhost:8001/v1", local=True))


def test_settings_cas_preserves_newer_plan_and_rejects_stale_generation(client, db_session, auth_user, auth_headers):
    from tests.test_course_generation_settings import _payload
    course = make_course(db_session, owner_id=auth_user.id)
    path = f"/api/courses/{course.id}/generation-settings"
    initial = client.put(path, json=_payload(expected_revision=0, approved_plan=["Approved title"]), headers=auth_headers)
    assert initial.status_code == 200
    assert initial.json()["revision"] == 1
    newer = client.put(path, json=_payload(expected_revision=1, approved_plan=["New approved title"]), headers=auth_headers)
    assert newer.status_code == 200 and newer.json()["revision"] == 2
    stale = client.put(path, json=_payload(expected_revision=1, approved_plan=["Old title"]), headers=auth_headers)
    assert stale.status_code == 409
    assert client.get(path, headers=auth_headers).json()["approved_plan"] == ["New approved title"]


def test_upload_body_cap_precedes_multipart_spooling(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_MAX_BYTES", 32)
    response = client.post("/api/courses/1/images", content=b"x" * (1024 * 1024 + 33),
        headers={**auth_headers, "Content-Type": "multipart/form-data; boundary=test"})
    assert response.status_code == 413


def test_learner_assistant_uses_snapshot_and_valid_citation_only(db_session, auth_user, monkeypatch):
    course = make_course(db_session, owner_id=auth_user.id)
    version = CourseVersion(course_id=course.id, revision=1, name="Published", publication_status="published")
    db_session.add(version); db_session.commit()
    assignment = SimpleNamespace(course_version_id=version.id)
    snapshot = {"modules": [{"lessons": [{"id": 5, "title": "Safe lesson", "content": "An atom contains a nucleus."}]}]}
    monkeypatch.setattr(assistant, "assignment_context", lambda *args: (assignment, snapshot))
    observed = []
    def generate(role, messages, **kwargs):
        observed.append(messages)
        return {"text": json.dumps({"answer": "The nucleus is central.", "cited_lesson_ids": [5], "unsupported": False}),
                "model": "reviewed-model", "usage": {"input_tokens": 20, "output_tokens": 10}}
    monkeypatch.setattr("app.ai.gateway.invoke_messages", generate)
    first = assistant.send_message(db_session, auth_user, 8, 5, "Where is the nucleus?", "question-123")
    assert first.status == "completed"
    assert first.citations[0]["course_version_id"] == version.id
    assert "correct_answer" not in json.dumps(observed)
    assert assistant.send_message(db_session, auth_user, 8, 5, "Where is the nucleus?", "question-123").id == first.id
    assert len(observed) == 1
    with pytest.raises(HTTPException) as exc:
        assistant.send_message(db_session, auth_user, 8, 99, "Unknown lesson", "question-456")
    assert exc.value.status_code == 404
    monkeypatch.setattr("app.ai.gateway.invoke_messages", lambda *a, **kw: {"text": json.dumps({"answer": "x", "cited_lesson_ids": [999]})})
    failed = assistant.send_message(db_session, auth_user, 8, 5, "Question", "question-789")
    assert failed.status == "failed"
    assert not failed.citations and failed.answer is None
