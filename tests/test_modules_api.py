from tests.factories import make_course


def test_module_crud(client, db_session):
    # сначала создаём курс, к которому привяжем модуль
    course = make_course(db_session)
    db_session.commit()

    # create
    payload = {
        "course_id": course.id,
        "title": "Module 1",
    }
    r = client.post(f"/api/courses/{course.id}/modules/", json=payload)
    assert r.status_code == 200
    mid = r.json()["id"]

    # get all for course
    r = client.get(f"/api/courses/{course.id}/modules/")
    assert r.status_code == 200
    assert any(m["id"] == mid for m in r.json())

    # update
    r = client.put(f"/api/modules/{mid}", json={"title": "Module 2"})
    assert r.status_code == 200
    assert r.json()["title"] == "Module 2"

    # delete
    r = client.delete(f"/api/modules/{mid}")
    assert r.status_code == 200

    r = client.get(f"/api/modules/{mid}")
    assert r.status_code in (404, 410)