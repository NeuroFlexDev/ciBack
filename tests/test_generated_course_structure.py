import json

import pytest

from app.models.course_generation_settings import CourseGenerationSettings
from app.models.document import Document, DocumentChunk
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.test import Test as TestModel
from app.models.theory import Theory
from app.models.task import Task
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
    assert set(body) == {"course_id", "title", "description", "status", "metrics", "modules_timeline"}
    assert body["status"] == "ready"
    assert [item["title"] for item in body["modules_timeline"]] == ["First", "Second"]
    assert [item["order"] for item in body["modules_timeline"]] == [1, 2]
    assert body["metrics"] == {"modules_count": 2, "lessons_count": 2, "tests_count": 0, "tasks_count": 0, "estimated_time_minutes": 3}
    module = client.get(f"/api/modules/{body['modules_timeline'][0]['module_id']}", headers=auth_headers)
    assert module.status_code == 200
    assert module.json()["lessons"][0]["content"] == "one"
    assert module.json()["tasks"] == []


def test_metrics_exclude_soft_deleted_and_include_assessments(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = Module(course_id=course.id, title="M", position=0)
    db_session.add(module); db_session.flush()
    lesson = Lesson(module_id=module.id, title="L", description="x", position=0)
    db_session.add_all([lesson, Lesson(module_id=module.id, title="Deleted", position=1, is_deleted=True)]); db_session.flush()
    db_session.add(Theory(lesson_id=lesson.id, content="word"))
    db_session.add(Task(module_id=module.id, name="Practice", description="Do it"))
    db_session.add_all([
        TestModel(module_id=module.id, assessment_scope="module", position=0, question="Q1", answers=json.dumps(["A"]), correct_answer="A"),
        TestModel(course_id=course.id, assessment_scope="final", position=0, question="Q2", answers=json.dumps(["B"]), correct_answer="B"),
        TestModel(course_id=course.id, assessment_scope="final", position=1, question="gone", answers="[]", correct_answer="", is_deleted=True),
    ])
    db_session.commit()
    body = client.get(f"/api/courses/{course.id}/structure", headers=auth_headers).json()
    assert body["metrics"] == {"modules_count": 1, "lessons_count": 1, "tests_count": 2, "tasks_count": 1, "estimated_time_minutes": 5}
    assert body["modules_timeline"][0]["tasks_count"] == 1
    detail = client.get(f"/api/modules/{module.id}", headers=auth_headers).json()
    assert detail["tasks"][0]["name"] == "Practice"


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


def test_materialization_builds_namespaced_test_and_task_canvas(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    nodes = [
        {"id": "mod:one", "type": "module", "label": "M", "description": "Module description"},
        {
            "id": "lesson:one", "type": "lesson", "label": "L", "content": "Content",
            "practices": [{"id": "practice:one", "title": "Practice", "instructions": "Do", "deliverable": "Result"}],
        },
        {
            "id": "test:module:one", "type": "test", "assessment_scope": "module",
            "questions": [{"id": "question:one", "question": "Q", "answers": ["A", "B"], "correct_answer": "A"}],
        },
    ]
    edges = [
        {"source": "mod:one", "target": "lesson:one", "relation": "contains"},
        {"source": "mod:one", "target": "test:module:one", "relation": "contains"},
    ]
    result = CourseMaterializationService.materialize(db_session, course=course, nodes=nodes, edges=edges)
    ids = {node["id"] for node in result["canvas_nodes"]}
    assert {item.split(":", 1)[0] for item in ids} == {"module", "lesson", "test", "task"}
    assert all(item.split(":", 1)[1].isdigit() for item in ids)
    logical = {node["logical_id"] for node in result["canvas_nodes"]}
    assert {"mod:one", "lesson:one", "question:one", "practice:one"} <= logical
    assert all(edge["source"] in ids and edge["target"] in ids for edge in result["canvas_edges"])
    assert db_session.query(Module).one().description == "Module description"


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
    db_session.refresh(course)
    assert all(
        node["id"].startswith(("module:", "lesson:", "test:", "task:"))
        for node in course.current_graph.nodes
    )
    assert {node["type"] for node in course.current_graph.nodes} == {"module", "lesson"}
    assert all(node.get("logical_id") for node in course.current_graph.nodes)


def test_generation_materialization_failure_rolls_back(db_session, auth_user, monkeypatch):
    course, run = _prepared_run(db_session, auth_user, 1)
    monkeypatch.setattr("app.services.pipeline_service.generate_from_prompt", lambda *a, **k: {"nodes": [{"id": "m", "type": "module"}, {"id": "l", "type": "lesson"}], "edges": []})
    with pytest.raises(PipelineRunFailed):
        PipelineService.generate_graph(db_session, course_id=course.id, owner_id=auth_user.id, force=False, prepared_run_id=run.id)
