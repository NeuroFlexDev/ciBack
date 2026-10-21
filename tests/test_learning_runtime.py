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
from app.models.learning import LearningAssignment, LearningResponse, LearningGradeEvent, PublishedLearningContent


def headers(user):
    return {'Authorization': 'Bearer ' + create_access_token(user.id, user.auth_version)}


@pytest.fixture
def world(db_session):
    a, b = Organization(name='Company'), Organization(name='Training center')
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
    meta = {'question_type': 'open', 'grading_mode': 'manual', 'max_points': 4, 'criteria': [{'id': 'accuracy', 'title': 'Accuracy', 'max_points': 4}], 'expected_revision': 0}
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
    h = headers(world['owner'])
    d = client.post('/api/team/departments', headers=h, json={'name': 'Engineering'}).json()['id']
    child = client.post('/api/team/departments', headers=h, json={'name': 'Team', 'parent_id': d}).json()['id']
    assert client.put(f'/api/team/departments/{d}', headers=h, json={'name': 'Engineering', 'parent_id': child}).status_code == 422
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
