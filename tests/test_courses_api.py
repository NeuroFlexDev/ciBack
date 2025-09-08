# tests/test_courses_api.py
from tests.factories import make_course


def test_course_crud(client, db_session):
    # create
    payload = {
        "title": "C1",
        "description": "desc",
        "level": 1,
        "language": 1,
    }
    r = client.post("/api/courses/", json=payload)
    assert r.status_code == 200
    cid = r.json()["id"]

    # get all
    r = client.get("/api/courses/")
    assert any(c["id"] == cid for c in r.json())

    # update
    r = client.put(f"/api/courses/{cid}", json={"title": "C2"})
    assert r.status_code == 200
    assert r.json()["title"] == "C2"

    # delete
    r = client.delete(f"/api/courses/{cid}")
    assert r.status_code == 200
