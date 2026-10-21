from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.models.learning import PublishedLearningContent, AssessmentDefinition
from app.models.materials import CourseImage
from app.models.theory import Theory
from app.services import image_service
from app.services.protected_image_refs import image_ids_in
from tests.test_learning_runtime import world, publish, assign, headers, grant
from tests.test_learning_support_export import operator, request, approve


def add_image(db, course, author):
    image = CourseImage(course_id=course.id, owner_id=author.id, status='approved', storage_key=f'{course.id}.png',
        mime_type='image/png', prompt='Private author generation prompt')
    db.add(image); db.commit()
    return image


@pytest.mark.parametrize('prefix', ['/api/images', '/images'])
@pytest.mark.parametrize('field', ['course', 'module', 'lesson', 'theory', 'task', 'question', 'rubric'])
def test_publication_rejects_foreign_images_in_all_snapshot_text(client, db_session, world, prefix, field):
    foreign, *_ = publish(client, db_session, world['foreign'], assessment=False, practice=False)
    image = add_image(db_session, foreign, world['foreign'])
    course, module, lesson, question, task, _ = publish(client, db_session, world['author'])
    reference = f'![Untrusted reference]({prefix}/{image.id}/content)'
    if field == 'course': course.description = reference
    elif field == 'module': module.description = reference
    elif field == 'lesson': lesson.description = reference
    elif field == 'theory': db_session.query(Theory).filter_by(lesson_id=lesson.id).one().content = reference
    elif field == 'task': task.description = reference
    elif field == 'question': question.question = reference
    elif field == 'rubric':
        definition = db_session.query(AssessmentDefinition).filter_by(task_id=task.id).one()
        definition.criteria = [{**definition.criteria[0], 'description': reference}]
    course.content_revision += 1
    db_session.commit()
    response = client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author']))
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'images_need_review'
    assert response.json()['detail']['image_ids'] == [image.id]


def test_historical_snapshot_reference_never_grants_foreign_or_other_course_image(client, db_session, world, monkeypatch):
    course, *_ , version = publish(client, db_session, world['author'])
    same_org, *_ = publish(client, db_session, world['author'], name='Unassigned private course')
    foreign, *_ = publish(client, db_session, world['foreign'])
    images = [add_image(db_session, course, world['author']), add_image(db_session, same_org, world['author']), add_image(db_session, foreign, world['foreign'])]
    # Simulate a release created before the stronger publication validation.
    row = db_session.get(PublishedLearningContent, version.id)
    snapshot = deepcopy(row.snapshot)
    snapshot['modules'][0]['assessments'][0]['description'] = ' '.join(f'![image](/images/{image.id}/content)' for image in images)
    row.snapshot = snapshot
    db_session.commit()
    assignment = assign(client, world, course)
    monkeypatch.setattr(image_service, 'storage', lambda: SimpleNamespace(read_bytes=lambda key: b'approved image'))
    for image in images:
        expected = 200 if image.course_id == course.id else 404
        for path in [f'/api/images/{image.id}', f'/api/images/{image.id}/content']:
            assert client.get(path, headers=headers(world['learner'])).status_code == expected
    grant(client, world, 'reviewer', 'reviewer', 'course', course.id)
    for image in images:
        assert client.get(f'/api/images/{image.id}/content', headers=headers(world['reviewer'])).status_code == (200 if image.course_id == course.id else 404)
    assert client.delete(f'/api/learning/assignments/{assignment["id"]}', headers=headers(world['owner'])).status_code == 200
    assert client.get(f'/api/images/{images[0].id}/content', headers=headers(world['learner'])).status_code == 404


@pytest.mark.parametrize('scope', ['course', 'assignment'])
def test_support_normalized_references_keep_course_and_tenant_boundaries(client, db_session, world, operator, monkeypatch, scope):
    course, _, lesson, _, task, version = publish(client, db_session, world['author'])
    foreign, *_ = publish(client, db_session, world['foreign'])
    own, other = add_image(db_session, course, world['author']), add_image(db_session, foreign, world['foreign'])
    text = f'![own](/images/{own.id}/content) ![foreign](/images/{other.id}/content)'
    task.description = text
    snapshot_row = db_session.get(PublishedLearningContent, version.id)
    snapshot = deepcopy(snapshot_row.snapshot)
    snapshot['modules'][0]['lessons'][0]['content'] = text
    snapshot_row.snapshot = snapshot
    db_session.commit()
    assignment = assign(client, world, course)
    permission = approve(client, world, request(client, operator, world, scope, course.id if scope == 'course' else assignment['id']))
    monkeypatch.setattr(image_service, 'storage', lambda: SimpleNamespace(read_bytes=lambda key: b'approved image'))
    path = f'/api/support/grants/{permission["id"]}/images'
    assert client.get(f'{path}/{own.id}/content', headers=headers(operator)).status_code == 200
    assert client.get(f'{path}/{other.id}/content', headers=headers(operator)).status_code == 404
    course.organization_id = world['other_org'].id
    db_session.commit()
    assert client.get(f'{path}/{own.id}/content', headers=headers(operator)).status_code == 404


def test_image_reference_normalization_is_exact_and_recursive():
    assert image_ids_in({'nested': ['![](/images/12/content)', {'body': '![](/api/images/13/content?version=1)'}]}) == {12, 13}
    assert image_ids_in('https://other.invalid/api/images/1/content /images/2/content-extra /images/3/content/extra') == set()
