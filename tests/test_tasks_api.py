from tests.auth_utils import auth_headers, register_and_login
from tests.factories import make_course, make_module


def test_task_crud(client, db_session):
    user, token = register_and_login(
        client,
        db_session,
        email="tasks@example.com",
        password="secret123",
        full_name="Task Owner",
    )
    course = make_course(db_session, owner_id=user.id)
    module = make_module(db_session, course_id=course.id)
    db_session.commit()

    create_response = client.post(
        f"/api/modules/{module.id}/tasks/",
        json={"name": "Task 1", "description": "Initial task"},
        headers=auth_headers(token),
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["id"]
    assert create_response.json()["is_deleted"] is False

    list_response = client.get(f"/api/modules/{module.id}/tasks/")
    assert list_response.status_code == 200
    assert any(task["id"] == task_id for task in list_response.json())

    get_response = client.get(f"/api/tasks/{task_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Task 1"

    update_response = client.put(
        f"/api/tasks/{task_id}",
        json={"description": "Updated task"},
        headers=auth_headers(token),
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated task"

    delete_response = client.delete(f"/api/tasks/{task_id}", headers=auth_headers(token))
    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Task deleted"}

    missing_response = client.get(f"/api/tasks/{task_id}")
    assert missing_response.status_code == 404
