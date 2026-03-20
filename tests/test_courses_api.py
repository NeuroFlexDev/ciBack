# tests/test_courses_api.py
from tests.auth_utils import auth_headers, register_and_login


def test_course_crud(client, db_session):
    _, token = register_and_login(
        client,
        db_session,
        email="courses@example.com",
        password="secret123",
        full_name="Course Owner",
    )

    # create
    payload = {
        "title": "C1",
        "description": "desc",
        "level": 1,
        "language": 1,
    }
    r = client.post("/api/courses/", json=payload, headers=auth_headers(token))
    assert r.status_code == 200
    cid = r.json()["id"]

    # get all
    r = client.get("/api/courses/")
    assert any(c["id"] == cid for c in r.json())

    # update
    r = client.put(f"/api/courses/{cid}", json={"title": "C2"}, headers=auth_headers(token))
    assert r.status_code == 200
    assert r.json()["title"] == "C2"

    # delete
    r = client.delete(f"/api/courses/{cid}", headers=auth_headers(token))
    assert r.status_code == 200
