from tests.factories import make_course, make_module


def test_lesson_crud(client, db_session, auth_user, auth_headers):
    course = make_course(db_session, owner_id=auth_user.id)
    module = make_module(db_session, course_id=course.id)
    db_session.commit()

    # create
    payload = {
        "module_id": module.id,
        "title": "Lesson 1",
        "description": "some content",
    }
    r = client.post(f"/api/courses/{course.id}/modules/{module.id}/lessons/", json=payload, headers=auth_headers)
    assert r.status_code == 200
    lid = r.json()["id"]

    # get all for module
    r = client.get(f"/api/courses/{course.id}/modules/{module.id}/lessons/", headers=auth_headers)
    assert r.status_code == 200
    assert any(l["id"] == lid for l in r.json())

    # update
    r = client.put(f"/api/lessons/{lid}", json={"title": "Lesson 2"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["title"] == "Lesson 2"

    # delete
    r = client.delete(f"/api/lessons/{lid}", headers=auth_headers)
    assert r.status_code == 200

    r = client.get(f"/api/lessons/{lid}", headers=auth_headers)
    assert r.status_code in (404, 410)
