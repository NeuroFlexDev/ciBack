# tests/test_course_generator_routes.py
from tests.factories import make_course, make_cs


def test_legacy_generator_cannot_erase_existing_content(client, db_session, auth_user, auth_headers):
    from tests.factories import make_module, make_lesson
    from app.models.theory import Theory
    c = make_course(db_session, owner_id=auth_user.id)
    cs = make_cs(db_session, course_id=c.id, owner_id=auth_user.id)
    module = make_module(db_session, course_id=c.id)
    lesson = make_lesson(db_session, module_id=module.id)
    theory = Theory(lesson_id=lesson.id, content="Author's saved lesson")
    db_session.add(theory); db_session.commit()
    response = client.get(f"/api/courses/{c.id}/generate_modules", params={"cs_id":cs.id}, headers=auth_headers)
    assert response.status_code == 410
    response = client.post(f"/api/courses/{c.id}/generate_lesson_content", json={"lesson_id":lesson.id}, headers=auth_headers)
    assert response.status_code == 410
    db_session.refresh(theory)
    assert theory.content == "Author's saved lesson"


def test_generate_modules_bad_cs(client, db_session, auth_user, auth_headers):
    c = make_course(db_session, owner_id=auth_user.id)
    r = client.get(f"/api/courses/{c.id}/generate_modules", params={"cs_id": 999}, headers=auth_headers)
    assert r.status_code == 404
