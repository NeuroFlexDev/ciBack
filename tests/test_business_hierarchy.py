from urllib.parse import urlsplit, parse_qs
import pytest
from app.models.user import User
from app.models.administration import Invitation
from app.models.learning import LearningAssignment, LearningPerson, LearningRoleGrant, LearningResponse, PublishedLearningContent
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.schemas.learning import LearningRules
from tests.test_business_positions import company, headers, make_position


@pytest.fixture
def hierarchy(db_session, company):
    org, users = company
    for key, role in [('second', 'manager'), ('candidate', 'candidate'), ('other_employee', 'member'), ('unassigned', 'member')]:
        users[key] = User(email=f'{key}@hierarchy.test', full_name=key, password_hash='not-a-login-password', organization_id=org.id, organization_role=role)
        db_session.add(users[key])
    db_session.flush()
    users['candidate'].manager_id = users['manager'].id
    users['other_employee'].manager_id = users['second'].id
    course = Course(owner_id=users['manager'].id, organization_id=org.id, name='Service', publication_status='published')
    db_session.add(course); db_session.flush()
    version = CourseVersion(course_id=course.id, name=course.name, revision=1, publication_status='published')
    db_session.add(version); db_session.flush()
    snapshot = {'course': {'id': course.id, 'title': course.name}, 'modules': [{'id': 1, 'title': 'Service', 'lessons': [
        {'id': 1, 'title': 'Service', 'content': 'Greet the guest', 'description': ''}], 'assessments': []}],
        'assessments': [{'id': 'test:1', 'question': 'What comes first?', 'description': '', 'revision': 1,
        'question_type': 'single', 'answers': ['Greet', 'Ignore'], 'correct_answers': ['Greet'], 'max_points': 1,
        'criteria': [], 'grading_mode': 'automatic'}]}
    db_session.add(PublishedLearningContent(course_version_id=version.id, organization_id=org.id, snapshot=snapshot))
    assignments = {}
    for key in ('employee', 'candidate', 'other_employee', 'unassigned'):
        assignments[key] = LearningAssignment(organization_id=org.id, course_version_id=version.id, user_id=users[key].id,
            assigned_by=users['owner'].id, rules_revision=1, rules=LearningRules().model_dump())
        db_session.add(assignments[key])
    db_session.commit()
    return org, users, course, assignments


def test_manager_team_and_reports_are_isolated_everywhere(client, db_session, hierarchy):
    _, u, course, assignments = hierarchy
    auth = headers(u['manager'])
    own = {u['employee'].id, u['candidate'].id}
    assert {r['id'] for r in client.get('/api/business/employees', headers=auth).json()['items']} == own
    assert {r['id'] for r in client.get('/api/team', headers=auth).json()['members']} == own | {u['manager'].id}
    assert {r['user_id'] for r in client.get('/api/team/results', headers=auth).json()['items']} == own
    assert {r['user_id'] for r in client.get(f'/api/courses/{course.id}/assignments', headers=auth).json()['items']} == own
    assert client.get(f"/api/team/results?user_id={u['other_employee'].id}", headers=auth).json()['items'] == []
    assert 'other_employee@' not in client.get('/api/team/results/export', headers=auth).text
    overview = client.get('/api/business/dashboard', headers=auth).json()
    assert overview['scope'] == 'team' and overview['employees'] == 1 and overview['candidates'] == 1
    assert overview['metrics']['assigned'] == 2 and overview['managers'] == []
    assert client.get('/api/learning/dashboard', headers=auth).json()['metrics']['people'] == 2
    for key in ('candidate', 'employee'):
        learner_auth = headers(u[key])
        assert client.get('/api/business/dashboard', headers=learner_auth).status_code == 403
        assert client.get('/api/team/results', headers=learner_auth).json()['items'] == []
        assert [p['id'] for p in client.get('/api/team', headers=learner_auth).json()['members']] == [u[key].id]
        assert client.get(f"/api/learning/assignments/{assignments['other_employee'].id}", headers=learner_auth).status_code == 404


def test_owner_dashboard_sees_all_managers_and_unassigned(client, hierarchy):
    _, u, _, _ = hierarchy
    data = client.get('/api/business/dashboard', headers=headers(u['owner'])).json()
    assert data['scope'] == 'company' and data['employees'] == 3 and data['candidates'] == 1
    assert data['unassigned'] == 1 and data['metrics']['assigned'] == 4
    managers = {m['id']: m for m in data['managers']}
    assert set(managers) == {u['manager'].id, u['second'].id}
    assert managers[u['manager'].id]['assigned'] == 2
    assert managers[u['second'].id]['assigned'] == 1


def test_foreign_person_mutations_and_indirect_assignment_are_denied(client, db_session, hierarchy):
    _, u, course, assignments = hierarchy
    auth = headers(u['manager']); other = u['other_employee']
    for suffix, payload in [('position', {'position_id': None, 'expected_position_id': None}),
                            ('access', {'is_active': False, 'expected_auth_version': 0}),
                            ('role', {'role': 'candidate', 'expected_auth_version': 0})]:
        assert client.put(f'/api/business/employees/{other.id}/{suffix}', headers=auth, json=payload).status_code == 404
    assert client.post(f'/api/courses/{course.id}/assignments', headers=auth, json={'user_ids': [other.id]}).status_code == 403
    assert client.delete(f"/api/learning/assignments/{assignments['other_employee'].id}", headers=auth).status_code == 403
    assert other.is_active and other.organization_role == 'member'


def test_owner_transfer_is_atomic_and_keeps_results(client, db_session, hierarchy):
    _, u, _, assignments = hierarchy
    target = u['candidate']; aid = assignments['candidate'].id
    db_session.add(LearningResponse(assignment_id=aid, assessment_id='test:1', attempt=1, revision=1, answer={'selected': ['Greet']}, status='reviewed', points=1))
    db_session.commit()
    payload = {'manager_id': u['second'].id, 'expected_manager_id': u['manager'].id, 'expected_auth_version': 0}
    endpoint = f'/api/business/employees/{target.id}/reporting-line'
    assert client.put(endpoint, headers=headers(u['manager']), json=payload).status_code == 403
    assert client.put(endpoint, headers=headers(u['owner']), json={**payload, 'manager_id': u['outsider'].id}).status_code == 409
    assert client.put(endpoint, headers=headers(u['owner']), json=payload).status_code == 200
    assert client.put(endpoint, headers=headers(u['owner']), json=payload).status_code == 409
    assert target.manager_id == u['second'].id
    assert aid not in {r['id'] for r in client.get('/api/team/results', headers=headers(u['manager'])).json()['items']}
    assert aid in {r['id'] for r in client.get('/api/team/results', headers=headers(u['second'])).json()['items']}
    assert db_session.query(LearningResponse).filter_by(assignment_id=aid).one().points == 1
    result = client.put(f'/api/business/employees/{target.id}/role', headers=headers(u['second']), json={'role': 'member', 'expected_auth_version': target.auth_version})
    assert result.status_code == 200 and result.json()['results_preserved']
    assert db_session.get(LearningAssignment, aid).user_id == target.id


def test_candidate_invitation_and_acceptance_preserve_reporting_line(client, db_session, hierarchy):
    _, u, _, _ = hierarchy
    position = make_position(client, u['manager'])
    payload = {'email': 'new.candidate@hierarchy.test', 'role': 'candidate', 'position_id': position['id']}
    assert client.post('/api/business/invitations', headers=headers(u['manager']), json={**payload, 'manager_id': u['second'].id}).status_code == 403
    response = client.post('/api/business/invitations', headers=headers(u['manager']), json=payload)
    assert response.status_code == 201, response.text
    assert response.json()['manager_id'] == u['manager'].id
    iid = response.json()['id']
    assert client.get('/api/business/invitations', headers=headers(u['second'])).json()['items'] == []
    assert client.delete(f'/api/business/invitations/{iid}', headers=headers(u['second'])).status_code == 404
    token = parse_qs(urlsplit(response.json()['url']).fragment)['token'][0]
    response = client.post('/api/auth/invitations/accept', json={'token': token, 'password': 'candidate-test-password-42',
        'full_name': 'Кандидат', 'terms_accepted': True, 'legal_version': client.get('/api/legal/catalog').json()['version']})
    assert response.status_code == 201, response.text
    candidate = db_session.get(User, response.json()['user']['id'])
    assert candidate.manager_id == u['manager'].id and candidate.organization_role == 'candidate'
    assert db_session.get(LearningPerson, candidate.id).position_id == position['id']


def test_candidate_reads_and_takes_test_but_cannot_author_even_with_old_grants(client, db_session, hierarchy):
    org, u, course, assignments = hierarchy
    candidate = u['candidate']; auth = headers(candidate); aid = assignments['candidate'].id
    db_session.add_all([LearningRoleGrant(organization_id=org.id, user_id=candidate.id, created_by=u['owner'].id, role=role,
        scope_type='organization', scope_id=0) for role in ('admin', 'author', 'reviewer')]); db_session.commit()
    permissions = client.get('/api/team/permissions', headers=auth).json()
    assert not any(permissions[k] for k in ('can_author', 'can_manage_team', 'can_report', 'can_review'))
    assert client.get(f'/api/courses/{course.id}/structure', headers=auth).status_code == 404
    content = client.get(f'/api/learning/assignments/{aid}', headers=auth)
    assert content.status_code == 200 and 'Greet the guest' in content.text
    assert 'correct_answers' not in content.text
    answer = client.post(f'/api/learning/assignments/{aid}/responses/test:1/submit', headers=auth,
        json={'expected_revision': 0, 'attempt': 1, 'answer': {'selected': ['Greet']}, 'submission_key': 'candidate-demo-submit'})
    assert answer.status_code == 200, answer.text
    assert answer.json()['points'] == 1
    assert client.put(f'/api/learning/assignments/{aid}/progress', headers=auth, json={'expected_revision': 0, 'lesson_id': 1}).status_code == 200
    assert client.get(f'/api/learning/assignments/{aid}', headers=auth).json()['status'] == 'completed'


def test_owner_handoff_keeps_four_roles_and_resolves_former_manager_team(client, db_session, hierarchy):
    _, u, _, assignments = hierarchy
    transfer = client.post('/api/team/ownership-transfers', headers=headers(u['owner']), json={'user_id': u['manager'].id})
    assert transfer.status_code == 201
    result = client.post(f"/api/team/ownership-transfers/{transfer.json()['id']}/accept", headers=headers(u['manager']))
    assert result.status_code == 200
    assert u['manager'].organization_role == 'owner'
    assert u['owner'].organization_role == 'manager'
    assert u['employee'].manager_id is None and u['candidate'].manager_id is None
    assert db_session.get(LearningAssignment, assignments['candidate'].id).user_id == u['candidate'].id
    data = client.get('/api/business/dashboard', headers=headers(u['manager'])).json()
    assert data['unassigned'] == 3 and data['metrics']['assigned'] == 4

def test_profile_edit_is_scoped_and_keeps_progress(client, db_session, hierarchy):
    _, users, _, assignments = hierarchy
    employee = users['employee']
    original_version = employee.auth_version
    endpoint = f'/api/business/employees/{employee.id}/profile'
    payload = {'full_name': 'Новое имя сотрудника', 'expected_full_name': employee.full_name}
    assert client.patch(endpoint, headers=headers(users['second']), json=payload).status_code == 404
    assert client.patch(endpoint, headers=headers(users['candidate']), json=payload).status_code == 403
    result = client.patch(endpoint, headers=headers(users['manager']), json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['full_name'] == payload['full_name']
    db_session.refresh(employee)
    assert employee.auth_version == original_version
    assert assignments['employee'].user_id == employee.id
    assert client.patch(endpoint, headers=headers(users['manager']), json=payload).status_code == 409
    assert client.patch(endpoint, headers=headers(users['manager']), json={**payload, 'full_name':'   '}).status_code == 422
    manager = users['manager']
    assert client.patch(f'/api/business/employees/{manager.id}/profile', headers=headers(users['owner']), json={'full_name':'Управляющая', 'expected_full_name':manager.full_name}).status_code == 200
    assert client.patch(f'/api/business/employees/{users["outsider"].id}/profile', headers=headers(users['owner']), json=payload).status_code == 404
