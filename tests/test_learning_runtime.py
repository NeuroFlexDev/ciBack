from datetime import datetime, timedelta
import json
import pytest

from app.core.security import create_access_token
from app.models.administration import Organization
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.theory import Theory
from app.models.test import Test as Question
from app.models.task import Task
from app.models.user import User
from app.models.learning import LearningAssignment, LearningResponse, LearningGradeEvent, PublishedLearningContent, AssessmentDefinition
from app.models.integrations import OutboundMessage, MobilePushPreference


def headers(user):
    return {'Authorization': 'Bearer ' + create_access_token(user.id, user.auth_version)}


@pytest.fixture
def world(db_session):
    # These contracts exercise the granular author/reviewer/group roles used by
    # education. B2B's four-role hierarchy has its own isolation contracts.
    a, b = Organization(name='Training organization', kind='education'), Organization(name='Training center', kind='education')
    db_session.add_all([a, b])
    db_session.flush()
    users = {}
    for name, role, org in [('owner', 'owner', a), ('admin', 'admin', a), ('author', 'author', a), ('reviewer', 'reviewer', a), ('manager', 'manager', a), ('learner', 'member', a), ('second', 'member', a), ('foreign', 'author', b)]:
        users[name] = User(email=name+'@example.test', full_name=name, password_hash='not-used-by-token-auth', organization_id=org.id, organization_role=role)
        db_session.add(users[name])
    db_session.commit()
    return {'org': a, 'other_org': b, **users}


def publish(client, db, author, *, name='Onboarding', assessment=True, practice=True):
    c = Course(name=name, description='Course', owner_id=author.id, status='ready')
    db.add(c)
    db.flush()
    m = Module(course_id=c.id, title='Module', position=0)
    db.add(m)
    db.flush()
    l = Lesson(module_id=m.id, title='Lesson', position=0)
    db.add(l)
    db.flush()
    db.add(Theory(lesson_id=l.id, content='Original published lesson.'))
    q = None
    if assessment:
        q = Question(module_id=m.id, assessment_scope='module', question='Choose', answers=json.dumps(['Correct', 'Wrong']), correct_answer='Correct', position=0)
        db.add(q)
    t = None
    if practice:
        t = Task(module_id=m.id, name='Explain', description='Describe the process')
        db.add(t)
        db.flush()
        db.add(AssessmentDefinition(task_id=t.id, question_type='open', grading_mode='manual', max_points=1,
            correct_answers=[], criteria=[{'id': 'explanation', 'title': 'Completeness of explanation', 'description': 'Describe the process correctly', 'max_points': 1}], revision=1))
    db.commit()
    result = client.post(f'/api/courses/{c.id}/publish', headers=headers(author))
    assert result.status_code == 200, result.text
    v = db.query(CourseVersion).filter_by(course_id=c.id).order_by(CourseVersion.id.desc()).first()
    return c, m, l, q, t, v


def assign(client, world, course, target=None, **kwargs):
    r = client.post(f'/api/courses/{course.id}/assignments', headers=headers(world['owner']), json={'user_ids': [target.id if target else world['learner'].id], **kwargs})
    assert r.status_code == 201, r.text
    return r.json()['items'][0]


def grant(client, world, name, role, scope_type, scope_id):
    r = client.post(f'/api/team/members/{world[name].id}/roles', headers=headers(world['owner']), json={'role': role, 'scope_type': scope_type, 'scope_id': scope_id})
    assert r.status_code == 201, r.text
    return r.json()


def test_management_directory_includes_unpublished_courses_without_content(client, db_session, world):
    own = Course(name='First draft', description='Private content', owner_id=world['author'].id)
    foreign = Course(name='Foreign draft', description='Secret', owner_id=world['foreign'].id)
    db_session.add_all([own, foreign])
    db_session.commit()
    result = client.get('/api/team/course-directory', headers=headers(world['owner']))
    assert result.status_code == 200
    assert result.json()['items'] == [{'id': own.id, 'title': 'First draft', 'status': own.status}]
    assert client.get('/api/team/course-directory', headers=headers(world['learner'])).status_code == 403
    assert client.get('/api/team/course-directory', headers=headers(world['foreign'])).status_code == 403


def test_disabled_authoring_blocks_new_writes_but_preserves_reads_and_existing_learning(client, db_session, world):
    from app.services.learning_service import settings_for
    course, module, lesson, _, _, _ = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    config = settings_for(db_session, world['org'].id)
    config.features = {**config.features, 'authoring': False}
    db_session.commit()
    author = headers(world['author'])
    assert client.get(f'/api/courses/{course.id}/structure', headers=author).status_code == 200
    for path, payload in [('/api/courses/drafts', {}), (f'/api/courses/{course.id}/modules/', {'title': 'Denied'})]:
        result = client.post(path, headers=author, json=payload)
        assert result.status_code == 403, result.text
        assert result.json()['detail']['code'] == 'feature_unavailable'
    assert client.put(f'/api/lessons/{lesson.id}', headers=author, json={'content': 'Denied', 'expected_revision': lesson.revision}).status_code == 403
    assert client.get(f'/api/learning/assignments/{assignment["id"]}', headers=headers(world['learner'])).status_code == 200
    assert client.put(f'/api/learning/assignments/{assignment["id"]}/progress', headers=headers(world['learner']),
        json={'expected_revision': 0, 'lesson_id': lesson.id, 'completed': True}).status_code == 200


def test_operator_delivery_profile_is_versioned_and_unavailable_to_customer_owner(client, db_session, world):
    operator = User(email='operator-profile@example.test', password_hash='unused', is_admin=True)
    db_session.add(operator); db_session.commit()
    path = f'/api/admin/organizations/{world["org"].id}/delivery'
    assert client.get(path, headers=headers(world['owner'])).status_code == 403
    current = client.get(path, headers=headers(operator)).json()
    payload = {'expected_revision': current['revision'], 'features': {'authoring': False}, 'delivery': {'deployment_mode': 'cloud'},
        'profile': 'training_center', 'languages': ['ru', 'en'], 'role_labels': {'member': 'Слушатель'}, 'ai_context': {'audience': 'New specialists'}}
    saved = client.put(path, headers=headers(operator), json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['profile'] == 'training_center'
    assert saved.json()['languages'] == ['ru', 'en']
    assert saved.json()['ai_context']['audience'] == 'New specialists'
    assert client.put(path, headers=headers(operator), json=payload).status_code == 409
def test_reminders_respect_frozen_window_membership_and_transaction_deduplication(client, db_session, world, monkeypatch):
    from app.services import mail_service
    from app.services.learning_reminders import queue_due_reminders
    from app.models.learning import LearningProgramEnrollment
    from app.models.administration import AdminEvent
    messages = []
    monkeypatch.setattr(mail_service, 'configured', lambda: True)
    monkeypatch.setattr(mail_service, 'queue_mail', lambda *args, **kwargs: messages.append((args[1:], kwargs)), raising=False)
    c, _, _, _, _, _ = publish(client, db_session, world['author'])
    now = datetime.utcnow()
    first = assign(client, world, c, due_at=(now + timedelta(days=2)).isoformat())
    second = assign(client, world, c, target=world['second'], due_at=(now + timedelta(days=2)).isoformat())
    # The window is frozen on enrollment, not read from a subsequently edited program.
    from app.models.learning import LearningProgram
    program = LearningProgram(organization_id=world['org'].id, title='Program', steps=[], reminder_days=10)
    db_session.add(program)
    db_session.flush()
    db_session.add(LearningProgramEnrollment(organization_id=world['org'].id, program_id=program.id,
        program_revision=1, user_id=world['second'].id, cycle_key='window-test',
        snapshot={'title': 'Program', 'reminder_days': 1}, assignment_ids=[second['id']]))
    db_session.commit()
    report = queue_due_reminders(db_session, now=now, limit=1)
    assert report['queued'] == 1 and report['more']
    next_report = queue_due_reminders(db_session, now=now, after_id=report['next_cursor'])
    assert next_report['queued'] == 0
    db_session.commit()
    assert queue_due_reminders(db_session, now=now)['queued'] == 0
    assert len(messages) == 1 and f'/learning/{first["id"]}' in messages[0][0][2]
    # A receipt is not retained if the mail transaction rolls back.
    future = now + timedelta(days=1, minutes=1)
    assert queue_due_reminders(db_session, now=future)['queued'] == 1
    db_session.rollback()
    assert queue_due_reminders(db_session, now=future)['queued'] == 1
    db_session.rollback()
    world['second'].is_active = False
    db_session.commit()
    assert queue_due_reminders(db_session, now=future)['queued'] == 0
    assert db_session.query(AdminEvent).filter_by(action='learning.reminder_queued').count() == 1
    monkeypatch.setattr(mail_service, 'configured', lambda: False)
    assert queue_due_reminders(db_session, now=future)['reason'] == 'mail_not_configured'


def test_reminders_skip_login_only_accounts_until_email_is_connected(client, db_session, world, monkeypatch):
    from app.services import mail_service
    from app.services.learning_reminders import queue_due_reminders
    from app.models.administration import AdminEvent
    messages = []
    monkeypatch.setattr(mail_service, 'configured', lambda: True)
    monkeypatch.setattr(mail_service, 'queue_mail', lambda db, recipient, *args, **kw: messages.append(recipient))
    course, *_ = publish(client, db_session, world['author'])
    now = datetime.utcnow()
    first = assign(client, world, course, due_at=(now + timedelta(days=1)).isoformat())
    assign(client, world, course, target=world['second'], due_at=(now + timedelta(days=1)).isoformat())
    world['learner'].email = None
    world['learner'].username = 'login.only.employee'
    db_session.commit()
    report = queue_due_reminders(db_session, now=now)
    db_session.commit()
    assert (report['scanned'], report['skipped'], report['queued']) == (2, 1, 1)
    assert messages == [world['second'].email]
    assert db_session.query(AdminEvent).filter_by(action='learning.reminder_queued', target_id=str(first['id'])).count() == 0
    world['learner'].email = 'verified@example.test'
    db_session.commit()
    assert queue_due_reminders(db_session, now=now)['queued'] == 1
    db_session.commit()
    assert messages[-1] == 'verified@example.test'
    assert queue_due_reminders(db_session, now=now)['queued'] == 0


def test_complete_snapshot_safe_learner_and_cross_tenant_isolation(client, db_session, world):
    course, module, lesson, question, task, version = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    path = f'/api/learning/assignments/{assignment["id"]}'
    data = client.get(path, headers=headers(world['learner'])).json()
    assert 'correct_answers' not in json.dumps(data)
    assert data['content']['modules'][0]['assessments'][1]['id'] == f'task:{task.id}'
    assert data['content']['modules'][0]['lessons'][0]['content'] == 'Original published lesson.'
    assert client.get(path, headers=headers(world['foreign'])).status_code == 404
    assert client.get(path, headers=headers(world['second'])).status_code == 404
    assert client.get(f'/api/tests/{question.id}', headers=headers(world['learner'])).status_code == 404
    assert client.get(f'/api/tasks/{task.id}', headers=headers(world['foreign'])).status_code == 404
    assert client.get(f'/api/modules/{module.id}/tasks/', headers=headers(world['learner'])).status_code == 404
    assert client.get(f'/api/versions/course/{course.id}', headers=headers(world['learner'])).status_code == 404
    changed = client.put(f'/api/lessons/{lesson.id}', headers=headers(world['author']), json={'content': 'Changed draft.', 'expected_revision': lesson.revision})
    assert changed.status_code == 200, changed.text
    assert client.get(path, headers=headers(world['learner'])).json()['content']['modules'][0]['lessons'][0]['content'] == 'Original published lesson.'
    assert client.post(f'/api/courses/{course.id}/publish', headers=headers(world['author'])).status_code == 200
    assert db_session.query(PublishedLearningContent).count() == 2
    assert client.get(path, headers=headers(world['learner'])).json()['course_version_id'] == version.id


def test_progress_and_drafts_survive_reload_conflict_and_submit_retry(client, db_session, world):
    c, _, lesson, question, task, _ = publish(client, db_session, world['author'])
    a = assign(client, world, c)
    base = f'/api/learning/assignments/{a["id"]}'
    h = headers(world['learner'])
    progress = client.put(base+'/progress', headers=h, json={'expected_revision': 0, 'lesson_id': lesson.id})
    assert progress.status_code == 200
    assert client.put(base+'/progress', headers=h, json={'expected_revision': 0, 'lesson_id': lesson.id}).status_code == 409
    draft_path = base+f'/responses/task:{task.id}'
    draft = client.put(draft_path, headers=h, json={'answer': {'text': 'My confirmed long answer'}, 'expected_revision': 0}).json()
    assert draft['revision'] == 1 and draft['status'] == 'draft'
    db_session.expire_all()
    restored = client.get(base, headers=h).json()
    assert restored['last_lesson_id'] == lesson.id
    assert restored['responses'][0]['answer']['text'] == 'My confirmed long answer'
    assert client.put(draft_path, headers=h, json={'answer': {'text': 'Stale overwrite'}, 'expected_revision': 0}).status_code == 409
    assert client.get(base, headers=h).json()['responses'][0]['answer']['text'] == 'My confirmed long answer'
    payload = {'answer': {'text': 'My confirmed long answer'}, 'expected_revision': 1, 'submission_key': 'retry-network-001'}
    first = client.post(draft_path+'/submit', headers=h, json=payload)
    retry = client.post(draft_path+'/submit', headers=h, json=payload)
    assert first.status_code == retry.status_code == 200
    assert first.json()['id'] == retry.json()['id']
    assert first.json()['status'] == 'pending_review'
    assert db_session.query(LearningResponse).filter_by(assignment_id=a['id']).count() == 1
    assert client.post(draft_path+'/submit', headers=h, json={**payload, 'answer': {'text': 'Different text'}}).status_code == 409


def test_progress_and_bookmark_operation_keys_are_idempotent_and_conflict_with_payload_change(client, db_session, world):
    c, _, lesson, _, _, _ = publish(client, db_session, world['author'])
    assignment = assign(client, world, c)
    base = f'/api/learning/assignments/{assignment["id"]}'
    h = headers(world['learner'])
    event = {'expected_revision': 0, 'operation_key': 'mobile-done-0001', 'lesson_id': lesson.id}
    first = client.put(base+'/progress', headers=h, json=event)
    retry = client.put(base+'/progress', headers=h, json=event)
    assert first.status_code == retry.status_code == 200
    assert retry.json()['progress_revision'] == first.json()['progress_revision'] == 1
    changed = client.put(base+'/progress', headers=h, json={**event, 'completed': False})
    assert changed.status_code == 409
    bookmark = {'expected_revision': 1, 'operation_key': 'mobile-place-0001', 'item_key': f'lesson:{lesson.id}'}
    first_bookmark = client.put(base+'/bookmark', headers=h, json=bookmark)
    second_bookmark = client.put(base+'/bookmark', headers=h, json=bookmark)
    assert first_bookmark.status_code == second_bookmark.status_code == 200
    assert second_bookmark.json()['progress_revision'] == 2
    conflict = client.put(base+'/bookmark', headers=h, json={**bookmark, 'operation_key': 'mobile-place-0002', 'expected_revision': 0})
    assert conflict.status_code == 409
    assert conflict.json()['detail']['current_revision'] == 2
    assert conflict.json()['detail']['assignment']['last_item_key'] == f'lesson:{lesson.id}'


def test_offline_manifest_is_gated_and_bound_to_assignment_snapshot(client, db_session, world):
    from app.services.learning_service import settings_for
    course, _, lesson, _, _, version = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    path = f"/api/learning/assignments/{assignment['id']}/offline-manifest"
    learner = headers(world['learner'])
    assert client.get(path, headers=learner).status_code == 403
    config = settings_for(db_session, world['org'].id)
    config.delivery = {'mobile_data_policy': {'offline_access': True, 'material_downloads': True, 'cache_ttl_hours': 48}}
    db_session.commit()
    mobile_policy = client.get('/api/auth/mobile/policy', headers=learner)
    assert mobile_policy.status_code == 200
    assert mobile_policy.json()['features'] == {'material_downloads': True, 'offline_access': True}
    result = client.get(path, headers=learner)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['course_version_id'] == version.id
    assert body['publication_revision'] == version.revision
    assert body['expires_at'] > body['issued_at']
    assert body['resources'] == []
    config.delivery = {'mobile_data_policy': {'material_downloads': True}}
    db_session.commit()
    assert client.get(path, headers=learner).status_code == 403
    assert client.get('/api/auth/mobile/policy', headers=learner).json()['features'] == {'material_downloads': True}
    revoked = client.delete(f"/api/learning/assignments/{assignment['id']}", headers=headers(world['owner']))
    assert revoked.status_code == 200
    assert client.get(path, headers=learner).status_code == 403


def test_offline_file_range_and_if_range_restart(tmp_path, monkeypatch):
    from hashlib import sha256
    from starlette.requests import Request
    from app.services import offline_files
    from app.services.file_storage import LocalFileStorage
    content = b'0123456789'
    key = 'pinned/file.bin'
    storage = LocalFileStorage(tmp_path, 1024)
    storage.save(__import__('io').BytesIO(content), key)
    monkeypatch.setattr(offline_files, 'get_file_storage', lambda: storage)
    def request(headers):
        scope = {'type': 'http', 'method': 'GET', 'path': '/', 'headers': [(k.encode(), v.encode()) for k, v in headers.items()]}
        return Request(scope)
    digest = sha256(content).hexdigest()
    partial = offline_files.file_response(request({'range': 'bytes=2-5'}), key, len(content), digest, 'application/octet-stream')
    assert partial.status_code == 206
    assert partial.headers['accept-ranges'] == 'bytes'
    assert partial.headers['etag'] == f'"{digest}"'
    import asyncio
    async def collect(response):
        return b''.join([chunk async for chunk in response.body_iterator])
    assert asyncio.run(collect(partial)) == b'2345'
    restarted = offline_files.file_response(request({'range': 'bytes=2-', 'if-range': '"stale"'}), key, len(content), digest, 'application/octet-stream')
    assert restarted.status_code == 200
    assert asyncio.run(collect(restarted)) == content
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as invalid:
        offline_files.file_response(request({'range': 'bytes=20-'}), key, len(content), digest, 'application/octet-stream')
    assert invalid.value.status_code == 416
    assert invalid.value.headers['Content-Range'] == f'bytes */{len(content)}'


def test_server_grading_reveal_policy_and_versioned_attempt_rules(client, db_session, world):
    c, _, lesson, question, _, _ = publish(client, db_session, world['author'], practice=False)
    settings = client.get('/api/team/settings', headers=headers(world['owner'])).json()
    update = {k: settings[k] for k in ['profile', 'languages', 'role_labels', 'ai_context', 'rules']}
    update['expected_revision'] = settings['revision']
    update['rules'] = {**settings['rules'], 'max_attempts': 2, 'reveal_answers': 'after_final_attempt'}
    assert client.put('/api/team/settings', headers=headers(world['owner']), json=update).status_code == 200
    a = assign(client, world, c)
    update['expected_revision'] += 1
    update['rules']['max_attempts'] = 1
    assert client.put('/api/team/settings', headers=headers(world['owner']), json=update).status_code == 200
    h = headers(world['learner'])
    base = f'/api/learning/assignments/{a["id"]}'
    submit = base+f'/responses/test:{question.id}/submit'
    cheating = client.post(submit, headers=h, json={'answer': {'selected': ['Wrong']}, 'points': 1, 'submission_key': 'bad-score-001'})
    assert cheating.status_code == 422
    one = client.post(submit, headers=h, json={'answer': {'selected': ['Wrong']}, 'submission_key': 'first-attempt'}).json()
    assert one['points'] == 0 and 'correct_answers' not in one
    two = client.post(submit, headers=h, json={'attempt': 2, 'answer': {'selected': ['Correct']}, 'submission_key': 'second-attempt'})
    assert two.status_code == 200, two.text
    assert two.json()['points'] == 1 and two.json()['correct_answers'] == ['Correct']
    assert client.post(submit, headers=h, json={'attempt': 3, 'answer': {'selected': ['Correct']}, 'submission_key': 'third-attempt'}).status_code == 409
    r = client.put(base+'/progress', headers=h, json={'expected_revision': 0, 'lesson_id': lesson.id})
    assert r.json()['status'] == 'completed'
    assert r.json()['rules']['max_attempts'] == 2
    assert db_session.query(LearningGradeEvent).count() == 2


def test_manual_review_scope_criteria_and_append_only_grade_history(client, db_session, world):
    c, _, _, _, task, _ = publish(client, db_session, world['author'], assessment=False)
    # Change assessment config and republish before assignment.
    meta = {'question_type': 'open', 'grading_mode': 'manual', 'max_points': 4, 'criteria': [{'id': 'accuracy', 'title': 'Accuracy', 'max_points': 4}], 'expected_revision': 1}
    assert client.put(f'/api/tasks/{task.id}/assessment-settings', headers=headers(world['author']), json=meta).status_code == 200
    assert client.post(f'/api/courses/{c.id}/publish', headers=headers(world['author'])).status_code == 200
    a = assign(client, world, c)
    sent = client.post(f'/api/learning/assignments/{a["id"]}/responses/task:{task.id}/submit', headers=headers(world['learner']), json={'answer': {'text': 'Explain'}, 'submission_key': 'manual-review'}).json()
    assert client.get('/api/learning/reviews', headers=headers(world['owner'])).json()['items'] == []
    assert client.get('/api/learning/reviews', headers=headers(world['reviewer'])).json()['items'] == []
    grant(client, world, 'reviewer', 'reviewer', 'course', c.id)
    assert len(client.get('/api/learning/reviews', headers=headers(world['reviewer'])).json()['items']) == 1
    endpoint = f'/api/learning/reviews/{sent["id"]}/grade'
    valid = {'expected_revision': sent['revision'], 'points': 3, 'feedback': 'Specific feedback', 'criteria_scores': [{'id': 'accuracy', 'points': 3}]}
    assert client.post(endpoint, headers=headers(world['owner']), json=valid).status_code == 404
    assert client.post(endpoint, headers=headers(world['reviewer']), json={**valid, 'points': 5}).status_code == 422
    graded = client.post(endpoint, headers=headers(world['reviewer']), json=valid)
    assert graded.status_code == 200, graded.text
    assert client.post(endpoint, headers=headers(world['reviewer']), json=valid).status_code == 409
    revised = {**valid, 'expected_revision': graded.json()['revision'], 'points': 4, 'criteria_scores': [{'id': 'accuracy', 'points': 4}], 'feedback': 'Reconsidered after review'}
    assert client.post(endpoint, headers=headers(world['reviewer']), json=revised).status_code == 200
    history = client.get(f'/api/learning/reviews/{sent["id"]}', headers=headers(world['reviewer'])).json()['history']
    assert [x['points'] for x in history] == [3, 4]
    assert history[1]['old_points'] == 3
    assert client.get(f'/api/learning/assignments/{a["id"]}/results', headers=headers(world['learner'])).json()['responses'][0]['feedback'] == revised['feedback']


def test_push_outbox_only_contains_assignments_and_confirmed_grade_revisions(client, db_session, world, monkeypatch):
    from pydantic import SecretStr
    from cryptography.fernet import Fernet
    from app.core.integration_config import integration_settings
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(integration_settings, 'EXPO_PUSH_ENABLED', True)
    c, _, _, _, task, _ = publish(client, db_session, world['author'], assessment=False)
    a = assign(client, world, c)
    push_rows = db_session.query(OutboundMessage).filter_by(kind='push').all()
    assert len(push_rows) == 1 and push_rows[0].deduplication_key == f'push:assignment:assignment:{a["id"]}:created'

    sent = client.post(f'/api/learning/assignments/{a["id"]}/responses/task:{task.id}/submit',
        headers=headers(world['learner']), json={'answer': {'text': 'A long enough submitted answer'}, 'submission_key': 'push-review-pending'}).json()
    assert sent['status'] == 'pending_review'
    assert db_session.query(OutboundMessage).filter(OutboundMessage.kind == 'push', OutboundMessage.deduplication_key.like('push:result:%')).count() == 0
    grant(client, world, 'reviewer', 'reviewer', 'course', c.id)
    endpoint = f'/api/learning/reviews/{sent["id"]}/grade'
    graded = client.post(endpoint, headers=headers(world['reviewer']), json={
        'expected_revision': sent['revision'], 'points': 1, 'feedback': 'Reviewed',
        'criteria_scores': [{'id': 'explanation', 'points': 1}]}).json()
    client.post(endpoint, headers=headers(world['reviewer']), json={
        'expected_revision': graded['revision'], 'points': 0, 'feedback': 'Revised',
        'criteria_scores': [{'id': 'explanation', 'points': 0}]})
    results = db_session.query(OutboundMessage).filter(OutboundMessage.kind == 'push', OutboundMessage.deduplication_key.like('push:result:%')).all()
    assert len(results) == 2


def test_push_deadline_scheduler_uses_local_calendar_and_deduplicates(client, db_session, world, monkeypatch):
    from datetime import timezone
    from pydantic import SecretStr
    from cryptography.fernet import Fernet
    from app.core.integration_config import integration_settings
    from app.services.mobile_push import enqueue_due_reminders
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(integration_settings, 'EXPO_PUSH_ENABLED', True)
    c, _, _, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    item = assign(client, world, c)
    assignment = db_session.get(LearningAssignment, item['id'])
    assignment.due_at = datetime(2026, 5, 10, 15, 0)  # 18:00 in Moscow
    db_session.add(MobilePushPreference(user_id=world['learner'].id, timezone='Europe/Moscow'))
    db_session.commit()
    now = datetime(2026, 5, 7, 6, 1, tzinfo=timezone.utc)  # 09:01, three local calendar days before
    first = enqueue_due_reminders(db_session, now=now)
    second = enqueue_due_reminders(db_session, now=now)
    assert first['queued'] == 1 and second['queued'] == 0
    reminder = db_session.query(OutboundMessage).filter_by(deduplication_key=f'push:deadline:{item["id"]}:2026-05-10').one()
    assert reminder.kind == 'push'


def test_push_delivery_is_session_bound_and_removes_unregistered_token(client, db_session, world, monkeypatch):
    import json
    from types import SimpleNamespace
    from pydantic import SecretStr
    from cryptography.fernet import Fernet
    from app.core.integration_config import integration_settings
    from app.models.integrations import MobileSession, MobilePushDevice
    from app.services.integration_secrets import seal
    from app.services.mobile_sessions import digest
    from app.services import mobile_push
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(integration_settings, 'EXPO_PUSH_ENABLED', True)
    c, _, _, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    item = assign(client, world, c)
    assignment = db_session.get(LearningAssignment, item['id'])
    session = MobileSession(id='push-session', user_id=world['learner'].id, refresh_token_hash='a' * 64,
        auth_version=world['learner'].auth_version, expires_at=datetime.utcnow() + timedelta(hours=1))
    db_session.add(session)
    token = 'ExpoPushToken[device-token]'
    device = MobilePushDevice(id='push-device', session_id=session.id, token_hash=digest(token),
        token_encrypted=seal(token), platform='ios')
    db_session.add(device)
    db_session.flush()
    seen = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _): return json.dumps({'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}).encode()

    def fake_urlopen(request, timeout):
        seen['request'] = json.loads(request.data)
        seen['timeout'] = timeout
        return Response()

    monkeypatch.setattr(mobile_push, 'urlopen', fake_urlopen)
    result = mobile_push.deliver(db_session, SimpleNamespace(id='outbox-1', organization_id=assignment.organization_id),
        {'assignment_id': assignment.id, 'type': 'assignment'})
    message = seen['request'][0]
    assert result == 'delivered' and seen['timeout'] == 10
    assert message['title'] == 'Новое назначение'
    assert message['data'] == {'type': 'assignment', 'assignment_id': assignment.id,
        'route': f'/learning/assignments/{assignment.id}'}
    assert c.name not in json.dumps(message)
    assert db_session.query(MobilePushDevice).count() == 0


def test_group_multiple_membership_future_enrollment_and_revoke_preserves_history(client, db_session, world):
    c, _, _, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    h = headers(world['owner'])
    groups = [client.post('/api/team/groups', headers=h, json={'name': n}).json()['id'] for n in ['Pilot', 'Safety']]
    for gid in groups:
        assert client.post(f'/api/team/groups/{gid}/members', headers=h, json={'user_ids': [world['learner'].id]}).status_code == 200
    r = client.post(f'/api/courses/{c.id}/assignments', headers=h, json={'group_ids': groups, 'include_future_members': True})
    assert r.status_code == 201, r.text
    assert r.json()['created'] == 1
    assert client.post(f'/api/courses/{c.id}/assignments', headers=h, json={'group_ids': groups, 'include_future_members': True}).json()['created'] == 0
    assert client.post(f'/api/team/groups/{groups[0]}/members', headers=h, json={'user_ids': [world['second'].id]}).status_code == 200
    assert len(client.get('/api/learning/assignments', headers=headers(world['second'])).json()['items']) == 1
    aid = r.json()['items'][0]['id']
    assert client.delete(f'/api/team/groups/{groups[0]}/members/{world["learner"].id}', headers=h).status_code == 200
    assert client.get(f'/api/learning/assignments/{aid}', headers=headers(world['learner'])).status_code == 200
    assert client.delete(f'/api/learning/assignments/{aid}', headers=h).status_code == 200
    assert client.get(f'/api/learning/assignments/{aid}', headers=headers(world['learner'])).status_code == 403
    assert client.get(f'/api/learning/assignments/{aid}/results', headers=headers(world['learner'])).status_code == 200
    assert db_session.get(LearningAssignment, aid).state == 'revoked'


def test_department_cycles_manager_scope_and_no_self_escalation(client, db_session, world):
    # B2G retains explicit author grants; business management now has its own workflow.
    world['org'].kind = 'government'
    db_session.commit()
    h = headers(world['owner'])
    d = client.post('/api/team/departments', headers=h, json={'name': 'Engineering'}).json()['id']
    child = client.post('/api/team/departments', headers=h, json={'name': 'Team', 'parent_id': d}).json()['id']
    current = next(item for item in client.get('/api/team/departments', headers=h).json()['items'] if item['id'] == d)
    assert client.put(f'/api/team/departments/{d}', headers=h, json={'name': 'Engineering', 'parent_id': child, 'expected_state': current['state_token']}).status_code == 422
    assert client.post(f'/api/team/members/{world["admin"].id}/roles', headers=headers(world['admin']), json={'role': 'admin'}).status_code == 403
    assert client.post(f'/api/team/members/{world["learner"].id}/roles', headers=headers(world['admin']), json={'role': 'admin'}).status_code == 403
    assert client.post('/api/courses/drafts', headers=headers(world['learner'])).status_code == 403
    assert client.post('/api/courses/drafts', headers=headers(world['owner'])).status_code == 403
    c, _, _, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    grant(client, world, 'manager', 'manager', 'department', d)
    grant(client, world, 'manager', 'manager', 'course', c.id)
    assert client.put(f'/api/team/people/{world["learner"].id}', headers=h, json={'department_id': d, 'position': 'Engineer'}).status_code == 200
    assert client.post(f'/api/courses/{c.id}/assignments', headers=headers(world['manager']), json={'user_ids': [world['learner'].id]}).status_code == 201
    assert client.post(f'/api/courses/{c.id}/assignments', headers=headers(world['manager']), json={'user_ids': [world['second'].id]}).status_code == 403


def test_program_order_repeat_and_course_archive(client, db_session, world):
    first = publish(client, db_session, world['author'], name='First', assessment=False, practice=False)
    second = publish(client, db_session, world['author'], name='Second', assessment=False, practice=False)
    h = headers(world['owner'])
    program = client.post('/api/team/programs', headers=h, json={'title': 'Adaptation', 'steps': [{'course_version_id': first[-1].id, 'due_days': 7}, {'course_version_id': second[-1].id, 'due_days': 14}], 'recurrence_days': 365})
    assert program.status_code == 201, program.text
    enrolled = client.post(f'/api/team/programs/{program.json()["id"]}/enrollments', headers=h, json={'user_ids': [world['learner'].id]}).json()['items'][0]
    ids = [a['id'] for a in enrolled['assignments']]
    student = headers(world['learner'])
    assert client.get(f'/api/learning/assignments/{ids[1]}', headers=student).status_code == 409
    assert client.put(f'/api/learning/assignments/{ids[0]}/progress', headers=student, json={'expected_revision': 0, 'lesson_id': first[2].id}).json()['status'] == 'completed'
    assert client.get(f'/api/learning/assignments/{ids[1]}', headers=student).status_code == 200
    assert client.post(f'/api/team/program-enrollments/{enrolled["id"]}/next-cycle', headers=h).status_code == 409
    first[0].is_deleted = True
    db_session.commit()
    assert client.get(f'/api/learning/assignments/{ids[0]}', headers=student).status_code == 200
    assert client.get('/api/learning/programs', headers=student).json()['items'][0]['next_assignment_id'] == ids[1]


def test_independent_approval_digest_and_scoped_author_access(client, db_session, world):
    c, _, lesson, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    grant(client, world, 'second', 'author', 'course', c.id)
    assert client.get(f'/api/lessons/{lesson.id}', headers=headers(world['second'])).status_code == 200
    assert client.get(f'/api/lessons/{lesson.id}', headers=headers(world['owner'])).status_code == 404
    settings = client.get('/api/team/settings', headers=headers(world['owner'])).json()
    payload = {k: settings[k] for k in ['profile', 'languages', 'role_labels', 'ai_context', 'rules']}
    payload.update(expected_revision=settings['revision'], rules={**settings['rules'], 'require_independent_approval': True})
    assert client.put('/api/team/settings', headers=headers(world['owner']), json=payload).status_code == 200
    assert client.put(f'/api/lessons/{lesson.id}', headers=headers(world['author']), json={'expected_revision': 1, 'content': 'New draft'}).status_code == 200
    assert client.post(f'/api/courses/{c.id}/publish', headers=headers(world['author'])).status_code == 409
    grant(client, world, 'reviewer', 'reviewer', 'course', c.id)
    content = client.get(f'/api/courses/{c.id}/review-content', headers=headers(world['reviewer'])).json()
    assert client.post(f'/api/courses/{c.id}/approvals', headers=headers(world['reviewer']), json={'content_digest': content['content_digest'], 'decision': 'approved', 'comment': 'Reviewed'}).status_code == 201
    assert client.post(f'/api/courses/{c.id}/publish', headers=headers(world['author'])).status_code == 200


def test_existing_invitation_requires_same_authenticated_email_and_owner_acceptance(client, db_session, world):
    outsider = User(email='existing@example.test', full_name='Existing', password_hash='preserved-hash')
    db_session.add(outsider)
    db_session.commit()
    h = headers(world['owner'])
    invited = client.post('/api/team/invitations', headers=h, json={'organization_id': world['org'].id, 'email': outsider.email, 'role': 'author'})
    assert invited.status_code == 201, invited.text
    token = invited.json()['url'].split('#token=')[1]
    assert client.post('/api/auth/invitations/accept-existing', headers=headers(world['foreign']), json={'token': token}).status_code == 403
    accepted = client.post('/api/auth/invitations/accept-existing', headers=headers(outsider), json={'token': token})
    assert accepted.status_code == 200, accepted.text
    assert outsider.password_hash == 'preserved-hash'
    transfer = client.post('/api/team/ownership-transfers', headers=h, json={'user_id': world['admin'].id}).json()
    assert client.post(f'/api/team/ownership-transfers/{transfer["id"]}/accept', headers=headers(world['second'])).status_code == 410
    assert client.post(f'/api/team/ownership-transfers/{transfer["id"]}/accept', headers=headers(world['admin'])).status_code == 200
    db_session.refresh(world['admin'])
    db_session.refresh(world['owner'])
    assert world['admin'].organization_role == 'owner' and world['owner'].organization_role == 'admin'


def test_old_author_does_not_take_course_to_new_organization(client, db_session, world):
    c, _, lesson, _, _, _ = publish(client, db_session, world['author'])
    old_org = c.organization_id
    world['author'].organization_id = world['other_org'].id
    db_session.commit()
    assert c.organization_id == old_org
    assert client.get(f'/api/lessons/{lesson.id}', headers=headers(world['author'])).status_code == 404


def test_practice_crud_is_scoped_validated_and_conflict_safe(client, db_session, world):
    course, module, _, _, _, _ = publish(client, db_session, world['author'], assessment=False, practice=False)
    h = headers(world['author'])
    created = client.post(f'/api/modules/{module.id}/tasks/', headers=h, json={'name': 'Practice', 'description': 'Initial instructions'})
    assert created.status_code == 200, created.text
    item = created.json()
    assert item['revision'] == 1 and item['is_deleted'] is False
    path = f'/api/tasks/{item["id"]}'
    assert client.get(path, headers=h).json()['description'] == 'Initial instructions'
    updated = client.put(path, headers=h, json={'name': 'Practice', 'description': 'Updated instructions', 'expected_revision': 1})
    assert updated.status_code == 200 and updated.json()['revision'] == 2
    assert client.put(path, headers=h, json={'name': 'Stale', 'description': 'Lost write', 'expected_revision': 1}).status_code == 409
    assert client.get(path, headers=h).json()['description'] == 'Updated instructions'
    assert client.delete(path+'?expected_revision=1', headers=h).status_code == 409
    assert client.delete(path+'?expected_revision=2', headers=h).status_code == 200
    assert client.get(f'/api/modules/{module.id}/tasks/', headers=h).json() == []
