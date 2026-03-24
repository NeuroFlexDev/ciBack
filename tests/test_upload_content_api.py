import json

import app.models.test as test_model_module
from app.models.task import Task
from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_lesson, make_module


def test_generate_lesson_content_only_replaces_target_lesson_content(client, db_session, monkeypatch):
    user, token = register_and_login(
        client,
        db_session,
        email="lesson-content@example.com",
        password="secret123",
        full_name="Lesson Content Owner",
    )
    course = make_course(db_session, owner_id=user.id)
    module = make_module(db_session, course_id=course.id)
    lesson_one = make_lesson(db_session, module_id=module.id, title="Lesson one")
    lesson_two = make_lesson(db_session, module_id=module.id, title="Lesson two")

    db_session.add(
        Task(
            module_id=module.id,
            lesson_id=lesson_two.id,
            name="Keep me",
            description="Task for second lesson",
        )
    )
    db_session.add(
        test_model_module.Test(
            module_id=module.id,
            lesson_id=lesson_two.id,
            question="Keep this question",
            answers=json.dumps(["A", "B"]),
            correct_answer="A",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.upload_service.generate_from_prompt",
        lambda *args, **kwargs: {
            "theory": "Generated theory",
            "tasks": [{"name": "Generated task", "description": "Task for first lesson"}],
            "questions": [{"question": "Generated question", "answers": ["Yes", "No"], "correct": "Yes"}],
        },
    )

    response = client.post(
        f"/api/courses/{course.id}/generate_lesson_content",
        json={"lesson_id": lesson_one.id},
        headers=auth_headers(token),
    )
    assert response.status_code == 200

    lesson_one_tasks = (
        db_session.query(Task)
        .filter(Task.lesson_id == lesson_one.id, Task.is_deleted == False)
        .order_by(Task.id.asc())
        .all()
    )
    lesson_two_tasks = (
        db_session.query(Task)
        .filter(Task.lesson_id == lesson_two.id, Task.is_deleted == False)
        .order_by(Task.id.asc())
        .all()
    )
    lesson_one_tests = (
        db_session.query(test_model_module.Test)
        .filter(
            test_model_module.Test.lesson_id == lesson_one.id,
            test_model_module.Test.is_deleted == False,
        )
        .order_by(test_model_module.Test.id.asc())
        .all()
    )
    lesson_two_tests = (
        db_session.query(test_model_module.Test)
        .filter(
            test_model_module.Test.lesson_id == lesson_two.id,
            test_model_module.Test.is_deleted == False,
        )
        .order_by(test_model_module.Test.id.asc())
        .all()
    )

    assert [item.name for item in lesson_one_tasks] == ["Generated task"]
    assert [item.name for item in lesson_two_tasks] == ["Keep me"]
    assert [item.question for item in lesson_one_tests] == ["Generated question"]
    assert [item.question for item in lesson_two_tests] == ["Keep this question"]
