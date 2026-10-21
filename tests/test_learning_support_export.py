import csv
import io
import json
from datetime import datetime, timedelta
import pytest
from app.models.user import User
from app.models.learning import SupportAccessGrant, LearningResponse, LearningAssignment, LearningPerson
from app.models.administration import AdminEvent
from test_learning_runtime import world, headers, publish, assign


@pytest.fixture
def operator(db_session):
    user = User(email='operator@platform.test', full_name='Operator', password_hash='not-used', is_admin=True)
    db_session.add(user); db_session.commit()
    return user


def request(client, operator, world, scope_type, scope_id):
    response = client.post('/api/support/grants', headers=headers(operator), json={
        'organization_id': world['org'].id, 'scope_type': scope_type, 'scope_id': scope_id,
        'reason': 'Investigate reported saved-answer discrepancy', 'requested_hours': 2})
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, world, grant):
    response = client.post(f"/api/support/grants/{grant['id']}/decision", headers=headers(world['owner']),
        json={'expected_revision': grant['revision'], 'decision': 'approved', 'hours': 1})
    assert response.status_code == 200, response.text
    return response.json()


def test_support_requires_owner_consent_scopes_reads_and_never_grants_writes(client, db_session, world, operator):
    c, _, _, q, _, _ = publish(client, db_session, world['author'], practice=False)
    assignment = assign(client, world, c)
    db_session.add(LearningResponse(assignment_id=assignment['id'], assessment_id=f'test:{q.id}', attempt=1,
        answer={'selected': ['Wrong']}, status='reviewed', points=0, feedback='Try again', submission_key='submitted'))
    db_session.commit()
    grant = request(client, operator, world, 'assignment', assignment['id'])
    path = f"/api/support/grants/{grant['id']}"
    assert 'content' not in grant and 'responses' not in grant
    assert client.get(path+'/content', headers=headers(operator)).status_code == 403
    assert client.post(path+'/decision', headers=headers(world['admin']), json={'expected_revision': 1, 'decision': 'approved'}).status_code == 403
    world['foreign'].organization_role = 'owner'; db_session.commit()
    assert client.post(path+'/decision', headers=headers(world['foreign']), json={'expected_revision': 1, 'decision': 'approved'}).status_code == 404
    approved = approve(client, world, grant)
    result = client.get(path+'/content', headers=headers(operator))
    assert result.status_code == 200, result.text
    assert result.json()['responses'][0]['answer'] == {'selected': ['Wrong']}
    assert 'correct_answers' not in result.text
    assert client.get(f'/api/learning/assignments/{assignment["id"]}', headers=headers(operator)).status_code != 200
    assert client.get(f'/api/courses/{c.id}/structure', headers=headers(operator)).status_code == 404
    assert client.put(f'/api/courses/{c.id}', headers=headers(operator), json={'name': 'hacked'}).status_code == 404
    assert client.post(path+'/decision', headers=headers(world['owner']), json={'expected_revision': 1, 'decision': 'approved'}).status_code == 409
    result = client.post(path+'/revoke', headers=headers(world['owner']), json={'expected_revision': approved['revision']})
    assert result.status_code == 200
    assert client.get(path+'/content', headers=headers(operator)).status_code == 403
    actions = {e.action for e in db_session.query(AdminEvent).filter(AdminEvent.target_type == 'support_grant')}
    assert {'support.requested', 'support.approved', 'support.content_read', 'support.revoked'} <= actions
    assert all('answer' not in e.details for e in db_session.query(AdminEvent))


def test_support_expiry_operator_revocation_and_target_move_fail_closed(client, db_session, world, operator):
    c, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    grant = approve(client, world, request(client, operator, world, 'course', c.id))
    path = f"/api/support/grants/{grant['id']}/content"
    assert client.get(path, headers=headers(operator)).status_code == 200
    row = db_session.get(SupportAccessGrant, grant['id'])
    row.expires_at = datetime.utcnow() - timedelta(seconds=1); db_session.commit()
    assert client.get(path, headers=headers(operator)).status_code == 403
    row.expires_at = datetime.utcnow() + timedelta(hours=1); operator.is_admin = False; db_session.commit()
    assert client.get(path, headers=headers(operator)).status_code == 403
    operator.is_admin = True; c.organization_id = world['other_org'].id; db_session.commit()
    assert client.get(path, headers=headers(operator)).status_code == 404


def test_support_images_require_live_grant_and_the_same_course(client, db_session, world, operator, monkeypatch):
    from app.models.materials import CourseImage
    from app.models.theory import Theory
    from app.services import image_service
    from types import SimpleNamespace
    course, _, lesson, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    foreign_course, *_ = publish(client, db_session, world['foreign'], assessment=False, practice=False)
    own = CourseImage(course_id=course.id, owner_id=world['author'].id, status='approved', storage_key='own.png', mime_type='image/png')
    foreign = CourseImage(course_id=foreign_course.id, owner_id=world['foreign'].id, status='approved', storage_key='private.png', mime_type='image/png')
    db_session.add_all([own, foreign]); db_session.flush()
    db_session.query(Theory).filter_by(lesson_id=lesson.id).one().content = f'![own](/api/images/{own.id}/content) ![foreign](/api/images/{foreign.id}/content)'
    db_session.commit()
    monkeypatch.setattr(image_service, 'storage', lambda: SimpleNamespace(read_bytes=lambda key: b'image'))
    grant = approve(client, world, request(client, operator, world, 'course', course.id))
    prefix = f"/api/support/grants/{grant['id']}/images"
    result = client.get(f'{prefix}/{own.id}/content', headers=headers(operator))
    assert result.status_code == 200 and result.content == b'image'
    assert result.headers['Cache-Control'] == 'no-store'
    assert client.get(f'{prefix}/{foreign.id}/content', headers=headers(operator)).status_code == 404
    assert client.get(f'/api/images/{own.id}/content', headers=headers(operator)).status_code == 404
    client.post(f"/api/support/grants/{grant['id']}/revoke", headers=headers(world['owner']), json={'expected_revision': grant['revision']})
    assert client.get(f'{prefix}/{own.id}/content', headers=headers(operator)).status_code == 403


def test_support_request_limits_and_rejection_do_not_open_content(client, db_session, world, operator):
    course, *_ = publish(client, db_session, world['author'], assessment=False, practice=False)
    body = {'organization_id': world['org'].id, 'scope_type': 'course', 'scope_id': course.id, 'reason': 'Investigate an owner-reported issue', 'requested_hours': 25}
    assert client.post('/api/support/grants', headers=headers(operator), json=body).status_code == 422
    grant = request(client, operator, world, 'course', course.id)
    assert client.post('/api/support/grants', headers=headers(operator), json={**body,'requested_hours':2}).status_code == 409
    result = client.post(f"/api/support/grants/{grant['id']}/decision", headers=headers(world['owner']), json={'expected_revision': 1, 'decision': 'rejected', 'comment': 'No customer data access needed'})
    assert result.status_code == 200 and result.json()['status'] == 'rejected'
    assert client.get(f"/api/support/grants/{grant['id']}/content", headers=headers(operator)).status_code == 403


def test_people_export_scoped_escaped_and_audited(client, db_session, world):
    world['learner'].full_name = '  =HYPERLINK("https://example.test")'
    world['second'].is_active = False
    db_session.add(LearningPerson(user_id=world['learner'].id, organization_id=world['org'].id, position=' Engineer', attributes={'Site': 'North'}))
    db_session.commit()
    path = '/api/team/people/export'
    assert client.get(path, headers=headers(world['learner'])).status_code == 403
    result = client.get(path, headers=headers(world['admin']))
    assert result.status_code == 200, result.text
    rows = list(csv.DictReader(io.StringIO(result.text.lstrip('\ufeff'))))
    assert len(rows) == 7 and all(r['email'] != world['foreign'].email for r in rows)
    learner = next(r for r in rows if r['user_id'] == str(world['learner'].id))
    assert learner['full_name'].startswith("'  =") and json.loads(learner['attributes']) == {'Site': 'North'}
    assert any(r['is_active'] == 'False' for r in rows)
    assert db_session.query(AdminEvent).filter_by(action='learning.people_exported').count() == 1


def test_assessment_and_lesson_bookmarks_are_server_scoped_conflict_safe(client, db_session, world):
    c, _, lesson, question, _, _ = publish(client, db_session, world['author'], practice=False)
    a = assign(client, world, c)
    path = f'/api/learning/assignments/{a["id"]}/bookmark'
    body = {'item_key': f'test:{question.id}', 'expected_revision': 0}
    result = client.put(path, headers=headers(world['learner']), json=body)
    assert result.status_code == 200 and result.json()['last_item_key'] == body['item_key']
    assert result.json()['completed_lessons'] == [] and result.json()['progress_revision'] == 1
    assert client.put(path, headers=headers(world['learner']), json=body).status_code == 409
    assert client.put(path, headers=headers(world['second']), json={**body, 'expected_revision': 1}).status_code == 404
    assert client.put(path, headers=headers(world['learner']), json={'item_key': 'test:999999', 'expected_revision': 1}).status_code == 404
    refreshed = client.get(f'/api/learning/assignments/{a["id"]}', headers=headers(world['learner'])).json()
    assert refreshed['last_item_key'] == body['item_key']
    completed = client.put(f'/api/learning/assignments/{a["id"]}/progress', headers=headers(world['learner']), json={'lesson_id': lesson.id, 'expected_revision': 1, 'completed': True}).json()
    assert completed['last_item_key'] == f'lesson:{lesson.id}'
    assert completed['completed_lessons'] == [lesson.id]
