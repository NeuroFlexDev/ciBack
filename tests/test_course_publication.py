from app.core.security import create_access_token, hash_password
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.generation_run import GenerationRun
from app.models.lesson_version import LessonVersion
from app.models.module_version import ModuleVersion
from app.models.test_version import TestVersion
from app.models.user import User
from tests.factories import make_course, make_lesson, make_module


def _ready_course(db, owner_id, name="Publish me"):
    course = make_course(db, owner_id=owner_id, name=name)
    module = make_module(db, course_id=course.id, title="Module")
    lesson = make_lesson(db, module_id=module.id, title="Lesson")
    from app.models.theory import Theory
    db.add(Theory(lesson_id=lesson.id, content="Complete lesson content"))
    db.commit()
    return course, module, lesson


def test_manual_course_can_publish_after_complete_author_workflow(client, db_session, auth_headers):
    course = client.post('/api/courses/drafts', headers=auth_headers).json()
    course_id = course['id']
    updated = client.put(f'/api/courses/{course_id}', headers=auth_headers,
        json={'title': 'Manual course', 'description': 'Prepared by the author', 'expected_revision': 1})
    assert updated.status_code == 200, updated.text
    module = client.post(f'/api/courses/{course_id}/modules/', headers=auth_headers, json={'title': 'Module'}).json()
    lesson = client.post(f'/api/courses/{course_id}/modules/{module["id"]}/lessons/', headers=auth_headers,
        json={'title': 'Lesson', 'description': '', 'content': 'Complete explanation and examples.'})
    assert lesson.status_code == 201, lesson.text
    result = client.post(f'/api/courses/{course_id}/publish', headers=auth_headers)
    assert result.status_code == 200, result.text
    assert db_session.get(Course, course_id).status == 'ready'


def test_publication_requires_complete_practice_rubric_and_preserves_generated_scale(client, db_session, auth_user, auth_headers):
    from app.models.task import Task
    from app.models.assessment_rubric import AssessmentRubric
    from app.models.learning import PublishedLearningContent
    course, module, _ = _ready_course(db_session, auth_user.id)
    task = Task(module_id=module.id, name='Explain', description='Explain the process')
    db_session.add(task); db_session.commit()
    result = client.post(f'/api/courses/{course.id}/publish', headers=auth_headers)
    assert result.status_code == 422
    assert result.json()['detail']['assessments'][0]['id'] == f'task:{task.id}'
    db_session.add(AssessmentRubric(course_id=course.id, task_id=task.id, criteria=[
        {'id': 'accuracy', 'title': 'Accuracy', 'description': 'Correct reasoning', 'weight': 0.6,
         'levels': [{'title': 'Incorrect', 'description': 'Missing reasoning', 'score': 0}, {'title': 'Correct', 'description': 'Complete reasoning', 'score': 5}]},
        {'id': 'examples', 'title': 'Examples', 'description': 'Relevant examples', 'weight': 0.4,
         'levels': [{'title': 'Missing', 'description': 'No examples', 'score': 0}, {'title': 'Present', 'description': 'Examples explained', 'score': 5}]},
    ], levels=[])); db_session.commit()
    published = client.post(f'/api/courses/{course.id}/publish', headers=auth_headers)
    assert published.status_code == 200, published.text
    version = db_session.query(CourseVersion).filter_by(course_id=course.id).one()
    snapshot = db_session.get(PublishedLearningContent, version.id).snapshot
    question = snapshot['modules'][0]['assessments'][0]
    assert question['max_points'] == 5
    assert [criterion['max_points'] for criterion in question['criteria']] == [3, 2]
    assert question['criteria'][0]['levels'][1]['points'] == 3


def test_publish_ready_course_creates_snapshot_and_is_idempotent(client, db_session, auth_user, auth_headers):
    course, module, lesson = _ready_course(db_session, auth_user.id)

    first = client.post(f"/api/courses/{course.id}/publish", headers=auth_headers)
    second = client.post(f"/api/courses/{course.id}/publish", headers=auth_headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["publication_status"] == "published"
    assert first.json()["published_at"]
    assert first.json()["revision"] == 1
    db_session.refresh(course)
    assert course.publication_status == "published"
    assert course.published_at is not None
    assert db_session.query(CourseVersion).filter_by(course_id=course.id).count() == 1
    version = db_session.query(CourseVersion).filter_by(course_id=course.id).one()
    assert version.revision == 1
    assert db_session.query(ModuleVersion).filter_by(course_version_id=version.id, module_id=module.id).count() == 1
    module_version = db_session.query(ModuleVersion).filter_by(course_version_id=version.id).one()
    assert db_session.query(LessonVersion).filter_by(module_version_id=module_version.id, lesson_id=lesson.id).count() == 1
    assert db_session.query(TestVersion).filter_by(course_version_id=version.id).count() == 0


def test_edit_published_course_opens_draft_revision_and_republish(client, db_session, auth_user, auth_headers):
    course, module, _ = _ready_course(db_session, auth_user.id)
    assert client.post(f"/api/courses/{course.id}/publish", headers=auth_headers).status_code == 200

    edited = client.put(f"/api/modules/{module.id}", headers=auth_headers, json={"title": "Edited", "expected_revision": 1})
    assert edited.status_code == 200
    db_session.refresh(course)
    assert course.publication_status == "draft"
    assert course.content_revision == 2
    assert course.published_at is None

    republished = client.post(f"/api/courses/{course.id}/publish", headers=auth_headers)
    assert republished.status_code == 200
    assert republished.json()["revision"] == 2
    assert [item.revision for item in db_session.query(CourseVersion).filter_by(course_id=course.id).order_by(CourseVersion.revision)] == [1, 2]


def test_publish_preconditions_and_owner_isolation(client, db_session, auth_user, auth_headers):
    empty = make_course(db_session, owner_id=auth_user.id)
    assert client.post(f"/api/courses/{empty.id}/publish", headers=auth_headers).status_code == 422

    course, _, _ = _ready_course(db_session, auth_user.id, "Generating")
    course.status = "generating"
    db_session.commit()
    assert client.post(f"/api/courses/{course.id}/publish", headers=auth_headers).status_code == 409

    course.status = "ready"
    db_session.add(GenerationRun(owner_id=auth_user.id, course_id=course.id, run_type="graph_generation", status="running", progress_percent=20, attempt=1))
    db_session.commit()
    assert client.post(f"/api/courses/{course.id}/publish", headers=auth_headers).status_code == 409

    other = User(email="publisher-other@example.com", password_hash=hash_password("password123"))
    db_session.add(other)
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    assert client.post(f"/api/courses/{course.id}/publish", headers=headers).status_code == 404


def test_course_list_filters_counts_paginates_and_is_owner_isolated(client, db_session, auth_user, auth_headers):
    published, _, _ = _ready_course(db_session, auth_user.id, "Published")
    draft, _, _ = _ready_course(db_session, auth_user.id, "Draft")
    assert client.post(f"/api/courses/{published.id}/publish", headers=auth_headers).status_code == 200
    other = User(email="list-other@example.com", password_hash=hash_password("password123"))
    db_session.add(other)
    db_session.commit()
    _ready_course(db_session, other.id, "Foreign")

    all_courses = client.get("/api/courses/", headers=auth_headers)
    assert all_courses.status_code == 200
    assert {item["id"] for item in all_courses.json()} == {published.id, draft.id}
    assert all_courses.headers["x-total-count"] == "2"
    item = next(item for item in all_courses.json() if item["id"] == published.id)
    assert item["publication_status"] == "published"
    assert item["module_count"] == item["lesson_count"] == 1
    assert item['author_name'] == (auth_user.full_name or auth_user.username or auth_user.email)
    assert item['position_id'] is None and item['position_name'] is None

    only_published = client.get("/api/courses/?publication_status=published", headers=auth_headers)
    assert [item["id"] for item in only_published.json()] == [published.id]
    only_draft = client.get("/api/courses/?publication_status=draft", headers=auth_headers)
    assert [item["id"] for item in only_draft.json()] == [draft.id]
    page = client.get("/api/courses/?limit=1&offset=1", headers=auth_headers)
    assert len(page.json()) == 1
    assert page.headers["x-total-count"] == "2"
