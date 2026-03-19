from tests.factories import make_course, make_lesson, make_module


def test_theory_crud(client, db_session):
    course = make_course(db_session)
    module = make_module(db_session, course_id=course.id)
    lesson = make_lesson(db_session, module_id=module.id)
    db_session.commit()

    create_response = client.post(
        "/api/theories/",
        json={"lesson_id": lesson.id, "content": "Theory content"},
    )
    assert create_response.status_code == 200
    assert create_response.json()["lesson_id"] == lesson.id
    assert create_response.json()["is_deleted"] is False

    get_response = client.get(f"/api/theories/{lesson.id}")
    assert get_response.status_code == 200
    assert get_response.json()["content"] == "Theory content"

    update_response = client.put(
        f"/api/theories/{lesson.id}",
        json={"content": "Updated theory"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["content"] == "Updated theory"

    delete_response = client.delete(f"/api/theories/{lesson.id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Theory deleted"}

    missing_response = client.get(f"/api/theories/{lesson.id}")
    assert missing_response.status_code == 404
