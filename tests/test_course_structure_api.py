from tests.auth_utils import auth_headers, register_and_login


def test_course_structure_crud(client, db_session):
    _, token = register_and_login(
        client,
        db_session,
        email="editor@example.com",
        password="secret123",
        full_name="Editor User",
        role="editor",
    )
    create_response = client.post(
        "/api/course-structure/",
        json={
            "sections": 2,
            "tests_per_section": 1,
            "lessons_per_section": 3,
            "questions_per_test": 5,
            "final_test": True,
            "content_types": ["theory", "quiz"],
        },
        headers=auth_headers(token),
    )
    assert create_response.status_code == 200
    structure_id = create_response.json()["id"]
    assert create_response.json()["content_types"] == ["theory", "quiz"]
    assert create_response.json()["is_deleted"] is False

    list_response = client.get("/api/course-structure/")
    assert list_response.status_code == 200
    assert any(item["id"] == structure_id for item in list_response.json())

    get_response = client.get(f"/api/course-structure/{structure_id}")
    assert get_response.status_code == 200
    assert get_response.json()["sections"] == 2

    update_response = client.put(
        f"/api/course-structure/{structure_id}",
        json={"content_types": ["video"], "final_test": False},
        headers=auth_headers(token),
    )
    assert update_response.status_code == 200
    assert update_response.json()["content_types"] == ["video"]
    assert update_response.json()["final_test"] is False

    delete_response = client.delete(f"/api/course-structure/{structure_id}", headers=auth_headers(token))
    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Course structure deleted"}

    missing_response = client.get(f"/api/course-structure/{structure_id}")
    assert missing_response.status_code == 404
