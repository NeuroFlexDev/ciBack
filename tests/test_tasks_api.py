from tests.factories import make_course, make_module


def test_task_crud(client, db_session):
    course = make_course(db_session)
    module = make_module(db_session, course_id=course.id)
    db_session.commit()

    create_response = client.post(
        f"/api/modules/{module.id}/tasks/",
        json={"name": "Task 1", "description": "Initial task"},
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
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated task"

    delete_response = client.delete(f"/api/tasks/{task_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Task deleted"}

    missing_response = client.get(f"/api/tasks/{task_id}")
    assert missing_response.status_code == 404
