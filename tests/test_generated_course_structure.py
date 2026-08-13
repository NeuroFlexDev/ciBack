import json

import pytest

from app.models.course_generation_settings import CourseGenerationSettings
from app.models.document import Document, DocumentChunk
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.test import Test as TestModel
from app.models.theory import Theory
from app.models.user import User
from app.services.course_materialization_service import CourseMaterializationService
from app.services.pipeline_service import PipelineRunFailed, PipelineService
from tests.factories import make_course


def _generated_payload():
    return {
        "nodes": [
            {"id": "m2", "label": "Second", "type": "module"},
            {"id": "m1", "label": "First", "type": "module"},
            {"id": "l2", "label": "Lesson 2", "type": "lesson", "description": "D2", "content": "two " * 210},
            {"id": "l1", "label": "Lesson 1", "type": "lesson", "description": "D1", "content": "one"},
        ],
        "edges": [
            {"source": "m1", "target": "m2", "relation": "precedes"},
            {"source": "m1", "target": "l1", "relation": "contains"},
            {"source": "m1", "target": "l2", "relation": "contains"},
            {"source": "l1", "target": "l2", "relation": "precedes"},
        ],
    }


def test_materialization_and_step5_contract(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    course.name, course.level, course.language = "Generated", "basic", "en"
    payload = _generated_payload()
    result = CourseMaterializationService.materialize(db_session, course=course, **payload)
    course.status = "ready"
    db_session.commit()
    assert result["module_count"] == 2
    response = client.get(f"/api/courses/{course.id}/structure", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"course", "metrics", "modules", "final_tests"}
    assert [item["title"] for item in body["modules"]] == ["First", "Second"]
    assert [item["title"] for item in body["modules"][0]["lessons"]] == ["Lesson 1", "Lesson 2"]
    assert body["metrics"] == {"module_count": 2, "lesson_count": 2, "test_question_count": 0, "estimated_duration_minutes": 3}
    module = client.get(f"/api/modules/{body['modules'][0]['id']}", headers=auth_headers)
    assert module.status_code == 200
    assert module.json()["lessons"][0]["content"] == "one"


def test_metrics_exclude_soft_deleted_and_include_assessments(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = Module(course_id=course.id, title="M", position=0)
    db_session.add(module); db_session.flush()
    lesson = Lesson(module_id=module.id, title="L", description="x", position=0)
    db_session.add_all([lesson, Lesson(module_id=module.id, title="Deleted", position=1, is_deleted=True)]); db_session.flush()
    db_session.add(Theory(lesson_id=lesson.id, content="word"))
    db_session.add_all([
        TestModel(module_id=module.id, assessment_scope="module", position=0, question="Q1", answers=json.dumps(["A"]), correct_answer="A"),
        TestModel(course_id=course.id, assessment_scope="final", position=0, question="Q2", answers=json.dumps(["B"]), correct_answer="B"),
        TestModel(course_id=course.id, assessment_scope="final", position=1, question="gone", answers="[]", correct_answer="", is_deleted=True),
    ])
    db_session.commit()
    body = client.get(f"/api/courses/{course.id}/structure", headers=auth_headers).json()
    assert body["metrics"] == {"module_count": 1, "lesson_count": 1, "test_question_count": 2, "estimated_duration_minutes": 5}
    assert len(body["final_tests"]) == 1


def test_step5_tenant_isolation(client, db_session, auth_user, auth_headers):
    foreign = User(email="step5-foreign@example.com", password_hash="hash")
    db_session.add(foreign); db_session.commit()
    course = make_course(db_session, owner_id=foreign.id)
    module = Module(course_id=course.id, title="Secret", position=0)
    db_session.add(module); db_session.commit()
    assert client.get(f"/api/courses/{course.id}/structure", headers=auth_headers).status_code == 404
    assert client.get(f"/api/modules/{module.id}", headers=auth_headers).status_code == 404


def test_invalid_materialization_does_not_replace_existing_structure(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    existing = Module(course_id=course.id, title="Existing", position=0)
    db_session.add(existing); db_session.commit()
    with pytest.raises(ValueError):
        CourseMaterializationService.materialize(db_session, course=course, nodes=[{"id": "m", "type": "module"}, {"id": "l", "type": "lesson"}], edges=[])
    db_session.rollback(); db_session.refresh(existing)
    assert existing.is_deleted is False


def _prepared_run(db_session, owner, lesson_count):
    course = make_course(db_session, owner_id=owner.id)
    document = Document(storage_key="x", owner_id=owner.id, course_id=course.id, version=1, status="indexed", content_hash="h", source_type="upload", original_filename="x.txt", mime_type="text/plain", size_bytes=1)
    db_session.add(document); db_session.flush()
    db_session.add(DocumentChunk(document_id=document.id, document_version=1, text="Context", chunk_index=0, metadata_json={}))
    db_session.add(CourseGenerationSettings(course_id=course.id, goal="G", difficulty="basic", language="en", lesson_count=lesson_count, module_tests_enabled=False, final_test_enabled=False, created_by=owner.id, updated_by=owner.id))
    course.status = "configured"; db_session.commit()
    run = PipelineService.prepare_graph_generation(db_session, course_id=course.id, owner_id=owner.id, document_ids=[document.id])
    return course, run


def test_generation_completes_only_after_materialization(db_session, auth_user, monkeypatch):
    course, run = _prepared_run(db_session, auth_user, 2)
    monkeypatch.setattr("app.services.pipeline_service.generate_from_prompt", lambda *a, **k: _generated_payload())
    result = PipelineService.generate_graph(db_session, course_id=course.id, owner_id=auth_user.id, force=False, prepared_run_id=run.id)
    assert result.status == "completed"
    assert result.output["module_count"] == 2
    assert db_session.query(Module).filter_by(course_id=course.id, is_deleted=False).count() == 2


def test_generation_materialization_failure_rolls_back(db_session, auth_user, monkeypatch):
    course, run = _prepared_run(db_session, auth_user, 1)
    monkeypatch.setattr("app.services.pipeline_service.generate_from_prompt", lambda *a, **k: {"nodes": [{"id": "m", "type": "module"}, {"id": "l", "type": "lesson"}], "edges": []})
    with pytest.raises(PipelineRunFailed):
        PipelineService.generate_graph(db_session, course_id=course.id, owner_id=auth_user.id, force=False, prepared_run_id=run.id)
