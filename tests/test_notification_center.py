from datetime import datetime, timedelta
from types import SimpleNamespace
import json
import pytest
from pydantic import SecretStr
from cryptography.fernet import Fernet
from app.core.security import create_access_token
from app.models.user import User
from app.models.administration import Organization
from app.models.notifications import UserNotification
from app.models.integrations import MobilePushPreference, MobilePushDevice, MobileSession, OutboundMessage
from app.services.notifications import publish, queue, fanout, deliver_device, quiet_until, DeferredDelivery
from app.services.integration_secrets import seal, unseal
from tests.test_learning_runtime import world, publish as publish_course, assign, grant, headers


def note(db, user, key="event:1"):
    return publish(db, user_id=user.id, organization_id=user.organization_id, event_key=key,
        category="generation", resource_type="course", resource_id=1, body="Open your workspace")


def test_inbox_persists_without_push_and_is_idempotent(client, db_session, auth_user, auth_headers, monkeypatch):
    from app.core.integration_config import integration_settings
    monkeypatch.setattr(integration_settings, "EXPO_PUSH_ENABLED", False)
    row = note(db_session, auth_user)
    queue(db_session, row)
    assert note(db_session, auth_user).id == row.id
    db_session.commit()
    result = client.get('/api/notifications', headers=auth_headers).json()
    assert result['unread_count'] == 1
    assert result['items'][0]['id'] == row.id
    assert db_session.query(OutboundMessage).count() == 0


def test_inbox_ownership_tenant_change_pagination_and_read_watermark(client, db_session, auth_user, auth_headers):
    other = User(email='other@example.com', password_hash='unused')
    db_session.add(other)
    db_session.flush()
    private = note(db_session, other)
    for i in range(4):
        note(db_session, auth_user, f'event:{i}')
    db_session.commit()
    first = client.get('/api/notifications?limit=2', headers=auth_headers).json()
    second = client.get('/api/notifications?limit=2&before_id='+str(first['next_cursor']), headers=auth_headers).json()
    assert len({x['id'] for p in [first, second] for x in p['items']}) == 4
    assert second['next_cursor'] is None
    assert client.post(f'/api/notifications/{private.id}/read', headers=auth_headers).status_code == 404
    newest = note(db_session, auth_user, 'event:new')
    db_session.commit()
    client.post('/api/notifications/read-all', headers=auth_headers, json={'through_id': first['latest_id']})
    remaining = client.get('/api/notifications?unread=true', headers=auth_headers).json()
    assert [x['id'] for x in remaining['items']] == [newest.id]
    org = Organization(name='New company')
    db_session.add(org)
    db_session.flush()
    auth_user.organization_id = org.id
    db_session.commit()
    assert client.get('/api/notifications', headers=auth_headers).json()['items'] == []


def test_preferences_validate_and_remain_compatible_with_old_client(client, auth_headers):
    prefs = client.get('/api/notifications/preferences', headers=auth_headers).json()
    prefs.update(quiet_enabled=True, timezone='Europe/Moscow', quiet_start='22:00', quiet_end='09:00', review_enabled=False)
    assert client.put('/api/notifications/preferences', headers=auth_headers, json=prefs).status_code == 200
    assert client.put('/api/notifications/preferences', headers=auth_headers, json={**prefs, 'timezone':'bad/zone'}).status_code == 422
    assert client.put('/api/notifications/preferences', headers=auth_headers, json={**prefs, 'quiet_end':'22:00'}).status_code == 422
    assert client.put('/api/notifications/preferences', headers=auth_headers, json={**prefs, 'quiet_start':'25:00'}).status_code == 422
    old = client.get('/api/auth/mobile/push-preferences', headers=auth_headers).json()
    assert set(old) == {'assignment_enabled','deadline_enabled','result_enabled','timezone'}
    assert client.put('/api/auth/mobile/push-preferences', headers=auth_headers, json=old).status_code == 200
    assert client.get('/api/notifications/preferences', headers=auth_headers).json()['review_enabled'] is False


def test_quiet_hours_cross_midnight_and_dst():
    pref = SimpleNamespace(quiet_enabled=True, timezone='Europe/Moscow', quiet_start='22:00', quiet_end='09:00')
    assert quiet_until(pref, datetime(2026, 10, 30, 20)) == datetime(2026, 10, 31, 6)
    assert quiet_until(pref, datetime(2026, 10, 30, 6)) is None
    pref.timezone = 'Europe/Berlin'
    assert quiet_until(pref, datetime(2026, 10, 24, 22)) == datetime(2026, 10, 25, 8)


@pytest.fixture
def push_world(db_session, auth_user, monkeypatch):
    from app.core.integration_config import integration_settings
    monkeypatch.setattr(integration_settings, 'DATA_ENCRYPTION_KEY', SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(integration_settings, 'EXPO_PUSH_ENABLED', True)
    now = datetime.utcnow()
    for i in range(2):
        session = MobileSession(id=f'session-{i}', user_id=auth_user.id, auth_version=auth_user.auth_version,
            refresh_token_hash=f'hash-{i}', expires_at=now+timedelta(days=1))
        db_session.add(session)
        db_session.flush()
        db_session.add(MobilePushDevice(id=f'device-{i}', session_id=session.id, token_hash=f'token-{i}', token_encrypted=seal(f'ExponentPushToken[{i}]'), platform='android'))
    row = note(db_session, auth_user)
    db_session.commit()
    return row


def test_per_device_fanout_deduplicates_and_account_switch_cancels(db_session, auth_user, push_world):
    assert fanout(db_session, push_world.id) == 'delivered'
    assert fanout(db_session, push_world.id) == 'delivered'
    db_session.flush()
    jobs = db_session.query(OutboundMessage).filter_by(kind='notice_push').all()
    assert len(jobs) == 2
    payload = unseal(jobs[0].payload_encrypted)
    device = db_session.get(MobilePushDevice, payload['device_id'])
    device.token_hash = 'reassigned-installation'
    assert deliver_device(db_session, jobs[0], payload) == 'cancelled'


def test_push_private_payload_receipt_and_invalid_device(db_session, push_world, monkeypatch):
    from app.services import notifications
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps({'data':[{'status':'ok','id':'ticket-1'}]}).encode()
    def send(request, timeout):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(notifications, 'urlopen', send)
    fanout(db_session, push_world.id)
    row = db_session.query(OutboundMessage).filter_by(kind='notice_push').first()
    assert deliver_device(db_session, row, unseal(row.payload_encrypted)) == 'delivered'
    assert len(requests[0]) == 1
    assert requests[0][0]['data'] == {'notification_id':push_world.id}
    assert push_world.body not in requests[0][0]['body']
    assert db_session.query(OutboundMessage).filter_by(deduplication_key=f'push-receipt:{row.id}').count() == 1


def test_quiet_deferral_does_not_consume_retry_budget(db_session, auth_user, push_world, monkeypatch):
    from app.services import outbox_service
    queue(db_session, push_world)
    db_session.commit()
    future = datetime.utcnow()+timedelta(hours=2)
    def defer(*args): raise DeferredDelivery(future)
    monkeypatch.setattr(outbox_service, 'deliver', defer)
    assert outbox_service.process_one(db_session)
    row = db_session.query(OutboundMessage).one()
    assert row.status == 'pending' and row.attempts == 0 and row.available_at == future
    assert row.payload_encrypted and row.lease_token is None


@pytest.mark.parametrize('reason', ['read', 'disabled', 'revoked', 'auth_version'])
def test_delivery_rechecks_preferences_and_session_before_provider(db_session, auth_user, push_world, monkeypatch, reason):
    from app.services import notifications
    fanout(db_session, push_world.id)
    job = db_session.query(OutboundMessage).filter_by(kind='notice_push').first()
    payload = unseal(job.payload_encrypted)
    if reason == 'read':
        push_world.read_at = datetime.utcnow()
    elif reason == 'disabled':
        db_session.add(MobilePushPreference(user_id=auth_user.id, push_enabled=False))
        db_session.flush()
    elif reason == 'revoked':
        db_session.get(MobileSession, payload['session_id']).revoked_at = datetime.utcnow()
    else:
        auth_user.auth_version += 1
    def forbidden(*args, **kwargs):
        pytest.fail('Cancelled notification reached Expo')
    monkeypatch.setattr(notifications, 'urlopen', forbidden)
    assert deliver_device(db_session, job, payload) == 'cancelled'


def test_provider_invalid_device_is_removed_without_retrying_other_devices(db_session, push_world, monkeypatch):
    from app.services import notifications
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            return json.dumps({'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}).encode()
    monkeypatch.setattr(notifications, 'urlopen', lambda *args, **kwargs: Response())
    fanout(db_session, push_world.id)
    job = db_session.query(OutboundMessage).filter_by(kind='notice_push').first()
    payload = unseal(job.payload_encrypted)
    assert deliver_device(db_session, job, payload) == 'cancelled'
    db_session.flush()
    assert db_session.get(MobilePushDevice, payload['device_id']) is None
    assert db_session.query(MobilePushDevice).count() == 1


def test_scoped_reviewer_gets_pending_answer_but_other_members_do_not(client, db_session, world):
    course, _, _, _, task, _ = publish_course(client, db_session, world['author'])
    grant(client, world, 'second', 'reviewer', 'course', course.id)
    assignment = assign(client, world, course)
    result = client.post(f'/api/learning/assignments/{assignment["id"]}/responses/task:{task.id}/submit',
        headers=headers(world['learner']), json={'answer': {'text': 'My explanation'},
        'attempt': 1, 'expected_revision': 0, 'submission_key': 'scoped-review-notification'})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'pending_review'
    expected = client.get('/api/notifications?category=review', headers=headers(world['second'])).json()
    assert len(expected['items']) == 1
    assert expected['items'][0]['resource_id'] == result.json()['id']
    for name in ['learner', 'foreign', 'reviewer']:
        assert client.get('/api/notifications?category=review', headers=headers(world[name])).json()['items'] == []
