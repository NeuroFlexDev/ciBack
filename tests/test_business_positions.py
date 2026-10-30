from copy import deepcopy
from urllib.parse import urlsplit, parse_qs
import pytest
from app.core.security import create_access_token, hash_password
from app.models.administration import Organization
from app.models.user import User
from app.models.learning import LearningPerson, PublishedLearningContent, LearningAssignment
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.course_generation_settings import CourseGenerationSettings
from app.services.course_generation_settings_service import generation_settings_snapshot
from app.services.business_service import position_prompt


def headers(user):
    return {'Authorization': 'Bearer ' + create_access_token(user.id, user.auth_version)}


@pytest.fixture
def company(db_session):
    org = Organization(name='Тестовая компания', kind='business', seat_limit=30)
    other = Organization(name='Другая компания', kind='business')
    government = Organization(name='Госзаказчик', kind='government')
    db_session.add_all([org, other, government]); db_session.flush()
    users = {}
    for key, role, tenant in [('owner', 'owner', org), ('manager', 'manager', org), ('employee', 'member', org), ('outsider', 'manager', other), ('government', 'manager', government)]:
        users[key] = User(email=f'{key}@business.test', password_hash=hash_password('example-password-123'), organization_id=tenant.id, organization_role=role)
    db_session.add_all(users.values()); db_session.flush()
    users['employee'].manager_id = users['manager'].id
    db_session.commit()
    return org, users


def make_position(client, manager, name='Специалист поддержки'):
    response = client.post('/api/business/positions', headers=headers(manager), json={'name': name, 'responsibilities': 'Принимать обращения клиентов', 'competencies': 'Проверять решение до ответа'})
    assert response.status_code == 201, response.text
    return response.json()


def test_owner_manager_employee_position_course_flow(client, db_session, company):
    org, users = company
    owner, manager, employee = (users[k] for k in ('owner', 'manager', 'employee'))
    # Only the owner appoints a manager; the old employee session is invalidated.
    old_employee_token = headers(employee)
    denied = client.put(f'/api/business/employees/{employee.id}/manager', headers=headers(manager), json={'role': 'manager', 'expected_auth_version': 0})
    assert denied.status_code == 403
    assert client.put(f'/api/business/employees/{employee.id}/manager', headers=headers(owner), json={'role': 'manager', 'expected_auth_version': 0}).status_code == 200
    assert client.get('/api/auth/me', headers=old_employee_token).status_code == 401
    assert client.put(f'/api/business/employees/{employee.id}/manager', headers=headers(owner), json={'role': 'member', 'expected_auth_version': employee.auth_version}).status_code == 200
    assert client.put(f'/api/business/employees/{employee.id}/reporting-line', headers=headers(owner), json={
        'manager_id': manager.id, 'expected_manager_id': None, 'expected_auth_version': employee.auth_version}).status_code == 200
    position = make_position(client, manager)
    response = client.put(f'/api/business/employees/{employee.id}/position', headers=headers(manager), json={'position_id': position['id'], 'expected_position_id': None})
    assert response.status_code == 200, response.text
    response = client.post(f"/api/business/positions/{position['id']}/courses", headers=headers(manager), json={'title': 'Работа с обращениями', 'goal': 'Научить решать типовые обращения', 'expected_position_revision': 1})
    assert response.status_code == 201, response.text
    course_id = response.json()['course_id']
    course = db_session.get(Course, course_id)
    config = db_session.query(CourseGenerationSettings).filter_by(course_id=course_id).one()
    frozen = deepcopy(generation_settings_snapshot(course, config))
    assert frozen['position_context']['responsibilities'] == 'Принимать обращения клиентов'
    assert 'Специалист поддержки' in position_prompt(frozen['position_context'])
    assert client.get(f'/api/courses/{course_id}/generation-settings', headers=headers(owner)).status_code == 200
    assert client.get(f'/api/courses/{course_id}/generation-settings', headers=headers(employee)).status_code == 404
    update = client.put(f"/api/business/positions/{position['id']}", headers=headers(manager), json={'name': position['name'], 'responsibilities': 'Новые обязанности', 'expected_revision': 1})
    assert update.status_code == 200
    assert frozen['position_context']['responsibilities'] == 'Принимать обращения клиентов'
    course.publication_status = 'published'
    version = CourseVersion(course_id=course.id, name=course.name, revision=1, publication_status='published')
    db_session.add(version); db_session.flush()
    db_session.add(PublishedLearningContent(course_version_id=version.id, organization_id=org.id, snapshot={'modules': [], 'final_tests': []})); db_session.commit()
    endpoint = f"/api/business/positions/{position['id']}/courses/{course.id}/assign"
    response = client.post(endpoint, headers=headers(manager), json={'expected_revision': 2})
    assert response.status_code == 201, response.text
    assert response.json()['created'] == 1
    assert client.post(endpoint, headers=headers(manager), json={'expected_revision': 2}).json()['created'] == 0
    assignment = db_session.query(LearningAssignment).one()
    assert assignment.user_id == employee.id and assignment.course_version_id == version.id
    assert client.get('/api/team/permissions', headers=headers(manager)).json()['can_report'] is True
    assert client.get('/api/team/results', headers=headers(manager)).json()['metrics']['assigned'] == 1
    assert client.get('/api/team/results', headers=headers(users['outsider'])).json()['items'] == []
    assert client.get('/api/team/results', headers=headers(employee)).json()['items'] == []


def test_tenant_roles_revisions_and_archive(client, db_session, company):
    _, users = company
    position = make_position(client, users['manager'])
    for role in ('employee', 'government'):
        assert client.get('/api/business/positions', headers=headers(users[role])).status_code == 403
        assert client.post('/api/business/positions', headers=headers(users[role]), json={'name': 'Нельзя'}).status_code == 403
    assert client.get(f"/api/business/positions/{position['id']}/courses", headers=headers(users['outsider'])).status_code == 404
    assert client.put(f"/api/business/employees/{users['employee'].id}/position", headers=headers(users['outsider']), json={'position_id': position['id'], 'expected_position_id': None}).status_code == 404
    assert client.put(f"/api/business/positions/{position['id']}", headers=headers(users['manager']), json={'name': 'Новое имя', 'expected_revision': 9}).status_code == 409
    invite = client.post('/api/business/invitations', headers=headers(users['manager']), json={'email': 'new@business.test', 'position_id': position['id']})
    assert invite.status_code == 201, invite.text
    assert client.put(f"/api/business/positions/{position['id']}/state", headers=headers(users['manager']), json={'expected_revision': 1, 'is_archived': True}).status_code == 200
    assert client.post(f"/api/business/positions/{position['id']}/courses", headers=headers(users['manager']), json={'title': 'Не создавать', 'goal': 'Тест', 'expected_position_revision': 2}).status_code == 409
    token = parse_qs(urlsplit(invite.json()['url']).fragment)['token'][0]
    assert client.post('/api/auth/invitations/inspect', json={'token': token}).status_code == 410
    assert client.put(f"/api/business/positions/{position['id']}/state", headers=headers(users['manager']), json={'expected_revision': 2, 'is_archived': False}).status_code == 200


def test_invitation_assigns_position_and_manager_cannot_escalate(client, db_session, company):
    _, users = company
    position = make_position(client, users['manager'])
    for role in ('manager', 'owner', 'admin', 'author'):
        response = client.post('/api/business/invitations', headers=headers(users['manager']), json={'email': f'{role}-invite@business.test', 'role': role})
        assert response.status_code in (403, 422)
    response = client.post('/api/business/invitations', headers=headers(users['manager']), json={'email': 'new@business.test', 'position_id': position['id']})
    assert response.status_code == 201, response.text
    token = parse_qs(urlsplit(response.json()['url']).fragment)['token'][0]
    legal_version = client.get('/api/legal/catalog').json()['version']
    accepted = client.post('/api/auth/invitations/accept', json={'token': token, 'password': 'new-password-1234', 'full_name': 'Новый сотрудник', 'terms_accepted': True, 'legal_version': legal_version})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()['user']['organization_kind'] == 'business'
    person = db_session.get(LearningPerson, accepted.json()['user']['id'])
    assert person.position_id == position['id']
    # B2G remains scoped: a manager does not gain authoring or B2B rights.
    assert client.get('/api/team/permissions', headers=headers(users['government'])).json()['can_author'] is False


def test_generation_freezes_job_profile_and_recovers_repeated_request(client, db_session, company, monkeypatch):
    from app.models.business import BusinessPosition
    from app.models.generation_run import GenerationRun
    from tests.test_async_generation_launch import _settings, _document
    _, users = company
    manager = users['manager']
    position = make_position(client, manager)
    course_id = client.post(f"/api/business/positions/{position['id']}/courses", headers=headers(manager), json={
        'title': 'Работа с обращениями', 'goal': 'Решение обращений', 'expected_position_revision': 1}).json()['course_id']
    course = db_session.get(Course, course_id)
    document = _document(db_session, course, manager)
    queued = []
    monkeypatch.setattr('app.routes.pipeline.enqueue_generation', queued.append)
    payload = {'settings': _settings(), 'document_ids': [document.id], 'request_id': 'business-job-frozen'}
    first = client.post(f'/api/courses/{course_id}/generation-runs', headers=headers(manager), json=payload)
    assert first.status_code == 202, first.text
    job = db_session.get(GenerationRun, first.json()['run_id'])
    assert job.settings_snapshot['regulation_assessment_mode'] == 'test_only'
    db_session.get(BusinessPosition, position['id']).responsibilities = 'Другая работа'
    db_session.commit()
    repeated = client.post(f'/api/courses/{course_id}/generation-runs', headers=headers(manager), json=payload)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()['run_id'] == job.id
    assert job.settings_snapshot['position_context']['responsibilities'] == 'Принимать обращения клиентов'
    assert queued == [job.id]


def test_employee_access_revokes_session_and_preserves_owner_boundary(client, db_session, company):
    org, users = company
    manager, employee, owner = (users[k] for k in ('manager', 'employee', 'owner'))
    token = headers(employee)
    assert client.put(f'/api/business/employees/{owner.id}/access', headers=headers(manager), json={'is_active': False, 'expected_auth_version': 0}).status_code == 404
    assert client.put(f'/api/business/employees/{manager.id}/access', headers=headers(manager), json={'is_active': False, 'expected_auth_version': 0}).status_code == 404
    result = client.put(f'/api/business/employees/{employee.id}/access', headers=headers(manager), json={'is_active': False, 'expected_auth_version': 0})
    assert result.status_code == 200, result.text
    assert client.get('/api/auth/me', headers=token).status_code == 401
    org.seat_limit = 1; db_session.commit()
    assert client.put(f'/api/business/employees/{employee.id}/access', headers=headers(manager), json={'is_active': True, 'expected_auth_version': 1}).status_code == 409
    assert client.post('/api/business/invitations', headers=headers(owner), json={'email': 'invalid-email', 'role': 'manager'}).status_code == 422
