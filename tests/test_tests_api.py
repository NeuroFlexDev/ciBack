from tests.factories import make_course, make_module


def test_test_crud(client, db_session):
    course = make_course(db_session)
    module = make_module(db_session, course_id=course.id)
    db_session.commit()

    create_response = client.post(
        f"/api/modules/{module.id}/tests/",
        json={
            "question": "What is API?",
            "answers": ["A contract", "A database"],
            "correct": "A contract",
        },
    )
    assert create_response.status_code == 200
    test_id = create_response.json()["id"]
    assert create_response.json()["answers"] == ["A contract", "A database"]
    assert create_response.json()["is_deleted"] is False

    list_response = client.get(f"/api/modules/{module.id}/tests/")
    assert list_response.status_code == 200
    assert any(item["id"] == test_id for item in list_response.json())

    get_response = client.get(f"/api/tests/{test_id}")
    assert get_response.status_code == 200
    assert get_response.json()["correct"] == "A contract"

    update_response = client.put(
        f"/api/tests/{test_id}",
        json={
            "question": "What is FastAPI?",
            "answers": ["A framework", "A queue"],
            "correct": "A framework",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["question"] == "What is FastAPI?"
    assert update_response.json()["answers"] == ["A framework", "A queue"]

    delete_response = client.delete(f"/api/tests/{test_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Test deleted"}

    missing_response = client.get(f"/api/tests/{test_id}")
    assert missing_response.status_code == 404
