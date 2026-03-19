from app.models.course_version import CourseVersion
from app.models.lesson_version import LessonVersion
from app.models.module_version import ModuleVersion
from tests.factories import make_course


def test_versioning_endpoints_return_snapshot_history(client, db_session):
    course = make_course(db_session)
    course_version = CourseVersion(
        course_id=course.id,
        name="Course v1",
        description="Snapshot",
        level=1,
        language=1,
    )
    db_session.add(course_version)
    db_session.commit()
    db_session.refresh(course_version)

    module_version = ModuleVersion(course_version_id=course_version.id, title="Module snapshot")
    db_session.add(module_version)
    db_session.commit()
    db_session.refresh(module_version)

    lesson_version = LessonVersion(
        module_version_id=module_version.id,
        title="Lesson snapshot",
        description="Snapshot lesson",
    )
    db_session.add(lesson_version)
    db_session.commit()
    db_session.refresh(lesson_version)

    course_versions_response = client.get(f"/api/versions/course/{course.id}")
    assert course_versions_response.status_code == 200
    assert course_versions_response.json()[0]["id"] == course_version.id

    module_versions_response = client.get(f"/api/versions/course-version/{course_version.id}/modules")
    assert module_versions_response.status_code == 200
    assert module_versions_response.json()[0]["id"] == module_version.id

    lesson_versions_response = client.get(f"/api/versions/module-version/{module_version.id}/lessons")
    assert lesson_versions_response.status_code == 200
    assert lesson_versions_response.json()[0]["id"] == lesson_version.id


def test_legacy_versioning_endpoints_fail_explicitly(client):
    module_legacy_response = client.get("/api/versions/module/1")
    assert module_legacy_response.status_code == 410

    lesson_legacy_response = client.get("/api/versions/lesson/1")
    assert lesson_legacy_response.status_code == 410
