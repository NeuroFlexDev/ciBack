import pytest

from app.core.security import create_access_token, hash_password
from app.models.course_generation_settings import CourseGenerationSettings
from app.models.generation_run import GenerationRun
from app.models.user import User
from app.services.pipeline_service import PipelineRunFailed, PipelineService
from tests.factories import make_course


def _payload(**overrides):
    payload = {
        "title": "  Information Security  ",
        "goal": "  Teach secure working practices  ",
        "target_audience": "  New employees  ",
        "difficulty": "basic",
        "language": "en",
        "lesson_count": 1,
        "module_tests_enabled": True,
        "final_test_enabled": False,
    }
    payload.update(overrides)
    return payload


def test_put_get_and_idempotent_settings(
    client, db_session, auth_user, auth_headers
):
    course = make_course(db_session, owner_id=auth_user.id, name="Draft")
    course.status = "draft"
    db_session.commit()

    created = client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(),
        headers=auth_headers,
    )
    assert created.status_code == 200
    assert created.json()["title"] == "Information Security"
    assert created.json()["goal"] == "Teach secure working practices"
    assert created.json()["target_audience"] == "New employees"
    assert created.json()["course_status"] == "configured"

    first = db_session.query(CourseGenerationSettings).filter_by(
        course_id=course.id
    ).one()
    first_id = first.id
    first_created_at = first.created_at

    updated = client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(
            goal="Updated goal",
            target_audience="   ",
            difficulty="advanced",
            language="ru",
            lesson_count=100,
            module_tests_enabled=False,
            final_test_enabled=True,
        ),
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["target_audience"] is None
    assert updated.json()["lesson_count"] == 100
    assert db_session.query(CourseGenerationSettings).filter_by(
        course_id=course.id
    ).count() == 1
    db_session.refresh(first)
    assert first.id == first_id
    assert first.created_at == first_created_at
    db_session.refresh(course)
    assert course.name == "Information Security"

    restored = client.get(
        f"/api/courses/{course.id}/generation-settings", headers=auth_headers
    )
    assert restored.status_code == 200
    assert restored.json() == updated.json()


@pytest.mark.parametrize("lesson_count", [1, 100])
def test_lesson_count_boundaries_are_accepted(
    client, db_session, auth_user, auth_headers, lesson_count
):
    course = make_course(db_session, owner_id=auth_user.id)
    response = client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(lesson_count=lesson_count),
        headers=auth_headers,
    )
    assert response.status_code == 200


@pytest.mark.parametrize("lesson_count", [0, 101])
def test_invalid_lesson_count_is_rejected(
    client, db_session, auth_user, auth_headers, lesson_count
):
    course = make_course(db_session, owner_id=auth_user.id)
    assert client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(lesson_count=lesson_count),
        headers=auth_headers,
    ).status_code == 422


@pytest.mark.parametrize(
    "difficulty", ["internship", "basic", "intermediate", "advanced"]
)
def test_all_difficulties_are_accepted(
    client, db_session, auth_user, auth_headers, difficulty
):
    course = make_course(db_session, owner_id=auth_user.id)
    assert client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(difficulty=difficulty),
        headers=auth_headers,
    ).status_code == 200


def test_settings_validation_is_strict(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    url = f"/api/courses/{course.id}/generation-settings"
    invalid_payloads = [
        _payload(title="   "),
        _payload(goal="   "),
        _payload(difficulty="introductory"),
        _payload(language="de"),
        {key: value for key, value in _payload().items() if key != "module_tests_enabled"},
        {**_payload(), "unexpected": True},
    ]
    for payload in invalid_payloads:
        assert client.put(url, json=payload, headers=auth_headers).status_code == 422


def test_settings_acl_missing_and_generating_conflict(
    client, db_session, auth_user, auth_headers
):
    course = make_course(db_session, owner_id=auth_user.id)
    missing = client.get(
        f"/api/courses/{course.id}/generation-settings", headers=auth_headers
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "generation_settings_not_found"

    foreign = User(
        email="settings-foreign@example.com",
        password_hash=hash_password("password123"),
    )
    db_session.add(foreign)
    db_session.commit()
    foreign_headers = {
        "Authorization": f"Bearer {create_access_token(foreign.id)}"
    }
    assert client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(),
        headers=foreign_headers,
    ).status_code == 404

    course.status = "generating"
    db_session.commit()
    assert client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(),
        headers=auth_headers,
    ).status_code == 409


def test_ready_course_can_be_reconfigured(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    course.status = "ready"
    db_session.commit()
    response = client.put(
        f"/api/courses/{course.id}/generation-settings",
        json=_payload(),
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["course_status"] == "configured"


def test_generation_uses_snapshot_and_settings_in_prompt(
    db_session, auth_user, monkeypatch
):
    from tests.test_upload_pipeline import _course_and_document
    from app.models.document import DocumentChunk

    course, document, _ = _course_and_document(db_session, auth_user)
    document.status = "indexed"
    document.chunks.append(
        DocumentChunk(
            document_id=document.id,
            document_version=document.version,
            text="Context",
            chunk_index=0,
            metadata_json={},
        )
    )
    db_session.commit()
    captured = {}

    def fake_generate(*args, **kwargs):
        captured.update(kwargs)
        return {
            "nodes": [
                {"id": "module-1", "label": "Module", "type": "module"},
                {"id": "lesson-1", "label": "Lesson", "type": "lesson"},
            ],
            "edges": [
                {"source": "module-1", "target": "lesson-1", "relation": "contains"}
            ],
        }

    monkeypatch.setattr("app.services.pipeline_service.generate_from_prompt", fake_generate)
    run = PipelineService.generate_graph(
        db_session, course_id=course.id, owner_id=auth_user.id, force=True
    )

    assert captured["goal"] == "Teach the topic"
    assert captured["difficulty"] == "basic"
    assert captured["lesson_count"] == 1
    assert run.settings_snapshot["module_tests_enabled"] is True
    stored = db_session.get(GenerationRun, run.id)
    assert stored.settings_snapshot == run.settings_snapshot


def test_settings_change_generation_fingerprint(
    db_session, auth_user, monkeypatch
):
    from tests.test_upload_pipeline import _course_and_document
    from app.models.document import DocumentChunk

    course, document, _ = _course_and_document(db_session, auth_user)
    document.status = "indexed"
    document.chunks.append(
        DocumentChunk(
            document_id=document.id,
            document_version=document.version,
            text="Context",
            chunk_index=0,
            metadata_json={},
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.pipeline_service.generate_from_prompt",
        lambda *args, **kwargs: {
            "nodes": [
                {"id": "module", "type": "module"},
                {"id": "lesson", "type": "lesson"},
            ],
            "edges": [{"source": "module", "target": "lesson"}],
        },
    )
    first = PipelineService.generate_graph(
        db_session, course_id=course.id, owner_id=auth_user.id, force=False
    )
    settings = db_session.query(CourseGenerationSettings).filter_by(
        course_id=course.id
    ).one()
    settings.difficulty = "advanced"
    course.status = "configured"
    db_session.commit()
    second = PipelineService.generate_graph(
        db_session, course_id=course.id, owner_id=auth_user.id, force=False
    )
    assert second.id != first.id
    assert second.input_fingerprint != first.input_fingerprint
    assert second.settings_snapshot["difficulty"] == "advanced"


def test_generation_fails_when_lesson_count_does_not_match(
    db_session, auth_user, monkeypatch
):
    from tests.test_upload_pipeline import _course_and_document
    from app.models.document import DocumentChunk

    course, document, _ = _course_and_document(db_session, auth_user)
    document.status = "indexed"
    document.chunks.append(
        DocumentChunk(
            document_id=document.id,
            document_version=document.version,
            text="Context",
            chunk_index=0,
            metadata_json={},
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.pipeline_service.generate_from_prompt",
        lambda *args, **kwargs: {
            "nodes": [
                {"id": "module", "type": "module"},
                {"id": "lesson-1", "type": "lesson"},
                {"id": "lesson-2", "type": "lesson"},
            ],
            "edges": [],
        },
    )
    with pytest.raises(PipelineRunFailed):
        PipelineService.generate_graph(
            db_session, course_id=course.id, owner_id=auth_user.id, force=True
        )


def test_generation_settings_openapi_and_authentication(client):
    assert client.get("/api/courses/1/generation-settings").status_code == 401
    assert client.put(
        "/api/courses/1/generation-settings", json=_payload()
    ).status_code == 401
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/api/courses/{course_id}/generation-settings"]
    assert set(path) == {"get", "put"}
    assert "CourseGenerationSettingsUpdate" in schema["components"]["schemas"]
    assert "CourseGenerationSettingsResponse" in schema["components"]["schemas"]
