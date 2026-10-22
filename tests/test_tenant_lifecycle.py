"""Reversible tenant management must preserve immutable learning and deliveries."""
import copy
import json
from datetime import datetime
from uuid import uuid4

from app.models.learning import LearningAssignment, LearningGroupEnrollment, LearningProgramEnrollment
from app.models.integrations import OutboundMessage, WebhookEndpoint
from app.services import outbox_service
from app.services.integration_secrets import seal, unseal
from test_learning_runtime import world, publish, headers
from test_integrations_operations import encryption, owner


def test_group_archive_restore_preserves_members_assignments_and_does_not_resume_auto_enrollment(client, db_session, world):
    h = headers(world['owner'])
    group = client.post('/api/team/groups', headers=h, json={'name': 'Safety'}).json()
    assert client.post(f"/api/team/groups/{group['id']}/members", headers=h,
        json={'user_ids': [world['learner'].id]}).status_code == 200
    course, *_, version = publish(client, db_session, world['author'])
    assigned = client.post(f'/api/courses/{course.id}/assignments', headers=h,
        json={'group_ids': [group['id']], 'include_future_members': True})
    assert assigned.status_code == 201, assigned.text
    assignment_ids = [row.id for row in db_session.query(LearningAssignment).all()]
    base = f"/api/team/groups/{group['id']}"
    edited = client.put(base, headers=h, json={'name': 'Safety updated', 'expected_state': group['state_token']})
    assert edited.status_code == 200
    assert client.post(base+'/archive', headers=h, json={'expected_state': group['state_token']}).status_code == 409
    assert client.post(base+'/archive', headers=h, json={'expected_state': edited.json()['state_token']}).status_code == 200
    assert client.get('/api/team/groups', headers=h).json()['items'] == []
    archived = client.get('/api/team/groups?archived=true', headers=h).json()['items'][0]
    assert client.get('/api/team/groups?archived=true', headers=headers(world['learner'])).status_code == 403
    world['foreign'].organization_role = 'owner'
    db_session.commit()
    assert client.post(base+'/restore', headers=headers(world['foreign']), json={'expected_state': archived['state_token']}).status_code == 404
    restored = client.post(base+'/restore', headers=h, json={'expected_state': archived['state_token']})
    assert restored.status_code == 200
    assert restored.json()['user_ids'] == [world['learner'].id]
    assert restored.json()['automatic_enrollments_restored'] is False
    assert not db_session.query(LearningGroupEnrollment).filter_by(group_id=group['id']).one().is_active
    assert [row.id for row in db_session.query(LearningAssignment).all()] == assignment_ids


def test_department_restore_requires_active_parent_and_valid_state(client, world):
    h = headers(world['owner'])
    parent = client.post('/api/team/departments', headers=h, json={'name': 'Engineering'}).json()
    child = client.post('/api/team/departments', headers=h, json={'name': 'QA', 'parent_id': parent['id']}).json()
    assert client.put(f"/api/team/departments/{parent['id']}", headers=h,
        json={'name': 'Cycle', 'parent_id': child['id'], 'expected_state': parent['state_token']}).status_code == 422
    assert client.post(f"/api/team/departments/{parent['id']}/archive", headers=h,
        json={'expected_state': parent['state_token']}).status_code == 409
    for row in (child, parent):
        assert client.post(f"/api/team/departments/{row['id']}/archive", headers=h,
            json={'expected_state': row['state_token']}).status_code == 200
    archive = {row['id']: row for row in client.get('/api/team/departments?archived=true', headers=h).json()['items']}
    assert client.post(f"/api/team/departments/{child['id']}/restore", headers=h,
        json={'expected_state': archive[child['id']]['state_token']}).status_code == 409
    for row in (parent, child):
        response = client.post(f"/api/team/departments/{row['id']}/restore", headers=h,
            json={'expected_state': archive[row['id']]['state_token']})
        assert response.status_code == 200, response.text
    assert len(client.get('/api/team/departments', headers=h).json()['items']) == 2
    assert client.get('/api/team/departments?archived=true', headers=headers(world['learner'])).status_code == 403


def test_program_restore_keeps_enrollment_revision_and_history(client, db_session, world):
    h = headers(world['owner'])
    *_, version = publish(client, db_session, world['author'])
    program = client.post('/api/team/programs', headers=h,
        json={'title': 'Adaptation', 'steps': [{'course_version_id': version.id}]}).json()
    base = f"/api/team/programs/{program['id']}"
    assert client.post(base+'/enrollments', headers=h, json={'user_ids': [world['learner'].id]}).status_code == 201
    enrollment = db_session.query(LearningProgramEnrollment).filter_by(program_id=program['id']).one()
    before = copy.deepcopy(enrollment.snapshot)
    assignments = [row.id for row in db_session.query(LearningAssignment).all()]
    assert client.post(base+'/archive', headers=h, json={'expected_revision': program['revision']}).status_code == 200
    assert client.get('/api/team/programs', headers=h).json()['items'] == []
    archived = client.get('/api/team/programs?archived=true', headers=h).json()['items'][0]
    assert client.post(base+'/restore', headers=h, json={'expected_revision': program['revision']}).status_code == 409
    restored = client.post(base+'/restore', headers=h, json={'expected_revision': archived['revision']})
    assert restored.status_code == 200
    assert restored.json()['revision'] == program['revision'] + 2
    db_session.refresh(enrollment)
    assert enrollment.snapshot == before
    assert [row.id for row in db_session.query(LearningAssignment).all()] == assignments


def test_missing_version_cannot_blindly_mutate_tenant_metadata(client, world):
    h = headers(world['owner'])
    group = client.post('/api/team/groups', headers=h, json={'name': 'New'}).json()
    assert client.get('/api/team/management-capabilities', headers=h).json() == {
        'version': 1, 'tenant_archive_restore': True, 'webhook_update': True}
    dept = client.post('/api/team/departments', headers=h, json={'name': 'Team'}).json()
    responses = [
        client.put(f"/api/team/groups/{group['id']}", headers=h, json={'name': 'Unsafe'}),
        client.post(f"/api/team/groups/{group['id']}/archive", headers=h, json={}),
        client.delete(f"/api/team/groups/{group['id']}", headers=h),
        client.put(f"/api/team/departments/{dept['id']}", headers=h, json={'name': 'Unsafe'}),
        client.delete(f"/api/team/departments/{dept['id']}", headers=h),
    ]
    for response in responses:
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'client_refresh_required'
    assert client.get('/api/team/groups', headers=h).json()['items'][0]['name'] == 'New'
    assert client.get('/api/team/departments', headers=h).json()['items'][0]['name'] == 'Team'


def test_legacy_program_archive_requires_refresh_without_changing_revision(client, db_session, world):
    h = headers(world['owner'])
    *_, version = publish(client, db_session, world['author'])
    program = client.post('/api/team/programs', headers=h,
        json={'title': 'Adaptation', 'steps': [{'course_version_id': version.id}]}).json()
    response = client.delete(f"/api/team/programs/{program['id']}", headers=h)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'client_refresh_required'
    assert client.get('/api/team/programs', headers=h).json()['items'] == [program]


def test_webhook_update_pins_legacy_pending_destination_and_future_events_use_new_url(client, db_session, owner, auth_headers, monkeypatch):
    import app.routes.integrations as routes
    monkeypatch.setattr(routes, 'validate_endpoint', lambda url: None)
    old_url, new_url = 'https://old.example.test/hook', 'https://new.example.test/hook'
    created = client.post('/api/integrations/webhooks', headers=auth_headers,
        json={'url': old_url, 'events': ['assignment.created']}).json()
    row = OutboundMessage(id=str(uuid4()), organization_id=owner.organization_id, kind='webhook',
        deduplication_key=f"webhook:{created['id']}:old-event", status='failed', attempts=2,
        payload_encrypted=seal({'endpoint_id': created['id'], 'event': {'id': 'old-event', 'type': 'assignment.created'}}),
        available_at=datetime.utcnow())
    db_session.add(row); db_session.commit()
    old_id, old_key = row.id, row.deduplication_key
    response = client.put(f"/api/integrations/webhooks/{created['id']}", headers=auth_headers,
        json={'url': new_url, 'events': ['course.published'], 'enabled': True, 'expected_state': created['state_token']})
    assert response.status_code == 200, response.text
    assert 'secret' not in response.json() and created['secret'] not in response.text
    db_session.refresh(row)
    assert row.id == old_id and row.deduplication_key == old_key and row.attempts == 2 and row.status == 'failed'
    assert unseal(row.payload_encrypted)['endpoint_url'] == old_url
    assert unseal(row.payload_encrypted)['endpoint_secret'] == created['secret']
    outbox_service.enqueue_event(db_session, owner.organization_id, 'course.published', 'new-event', {'course_id': 123})
    db_session.commit()
    new = db_session.query(OutboundMessage).filter_by(deduplication_key=f"webhook:{created['id']}:new-event").one()
    assert unseal(new.payload_encrypted)['endpoint_url'] == new_url
    destinations = []
    monkeypatch.setattr(outbox_service.integration_http, 'request', lambda method, url, body, headers: destinations.append(url))
    assert outbox_service.deliver(db_session, row) == 'delivered'
    assert outbox_service.deliver(db_session, new) == 'delivered'
    assert destinations == [old_url, new_url]
    assert client.put(f"/api/integrations/webhooks/{created['id']}", headers=auth_headers,
        json={'url': old_url, 'events': ['assignment.created'], 'enabled': False, 'expected_state': created['state_token']}).status_code == 409


def test_webhook_update_revalidates_ssrf_and_disabling_stops_pending_delivery(client, db_session, owner, auth_headers, monkeypatch):
    import app.routes.integrations as routes
    def validate(url):
        if '127.0.0.1' in url:
            raise ValueError('private address')
    monkeypatch.setattr(routes, 'validate_endpoint', validate)
    created = client.post('/api/integrations/webhooks', headers=auth_headers,
        json={'url': 'https://public.example.test/hook', 'events': ['assignment.created']}).json()
    url = f"/api/integrations/webhooks/{created['id']}"
    legacy = client.delete(url, headers=auth_headers)
    assert legacy.status_code == 409
    assert legacy.json()['detail']['code'] == 'client_refresh_required'
    body = {'url': 'https://127.0.0.1/internal', 'events': ['assignment.created'], 'enabled': True, 'expected_state': created['state_token']}
    assert client.put(url, headers=auth_headers, json=body).status_code == 422
    outbox_service.enqueue_event(db_session, owner.organization_id, 'assignment.created', 'queued-event', {})
    db_session.commit()
    assert client.put(url, headers=auth_headers, json={**body, 'url': created['url'], 'enabled': False}).status_code == 200
    pending = db_session.query(OutboundMessage).one()
    monkeypatch.setattr(outbox_service.integration_http, 'request', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not send')))
    assert outbox_service.deliver(db_session, pending) == 'cancelled'
