from datetime import datetime

from app.models.administration import AdminEvent, Invitation
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.generation_run import GenerationRun
from app.models.legal import LegalReceipt, LegalRequest
from app.models.user import User
from test_administration import admin, org, member, headers, invite, acceptance
from test_learning_runtime import world, publish, assign


def test_all_new_management_routes_require_operator(client, auth_headers):
    cases = [('get', '/organizations/1', None), ('post', '/organizations/1/archive', {'expected_state': '0'*64}),
             ('get', '/users/1', None), ('post', '/users/1/restore', {'expected_auth_version': 0}),
             ('post', '/courses', {'owner_id': 1, 'title': 'Safe'}), ('get', '/courses/1', None),
             ('put', '/courses/1', {'title': 'Safe', 'expected_state': '0'*64}),
             ('post', '/courses/1/archive', {'expected_state': '0'*64}),
             ('post', '/invitations/1/resend', {}), ('get', '/requests/missing', None),
             ('put', '/requests/missing', {'status': 'open', 'expected_state': '0'*64})]
    for method, path, body in cases:
        kwargs = {'json': body} if body is not None else {}
        assert getattr(client, method)('/api/admin'+path, headers=auth_headers, **kwargs).status_code == 403
        assert getattr(client, method)('/api/admin'+path, **kwargs).status_code == 401


def test_organization_archive_restore_preserves_members_and_rejects_stale_edit(client, db_session, admin):
    tenant = org(db_session)
    user = member(db_session, tenant)
    h = headers(admin)
    original = client.get(f'/api/admin/organizations/{tenant.id}', headers=h).json()
    archived = client.post(f'/api/admin/organizations/{tenant.id}/archive', headers=h, json={'expected_state': original['state_token']})
    assert archived.status_code == 200, archived.text
    assert archived.json()['history_preserved'] and not archived.json()['is_active']
    assert client.get('/api/auth/me', headers=headers(user)).status_code == 403
    assert client.post(f'/api/admin/organizations/{tenant.id}/restore', headers=h, json={'expected_state': original['state_token']}).status_code == 409
    restored = client.post(f'/api/admin/organizations/{tenant.id}/restore', headers=h, json={'expected_state': archived.json()['state_token']})
    assert restored.status_code == 200 and restored.json()['is_active']
    assert db_session.get(User, user.id).organization_id == tenant.id


def test_user_edit_archive_and_restore_respect_version_last_owner_and_seats(client, db_session, admin):
    tenant = org(db_session, seat_limit=3)
    owner = member(db_session, tenant, 'tenant-owner@example.test', 'owner')
    target = member(db_session, tenant)
    token = headers(target)
    h = headers(admin)
    payload = {'is_active': True, 'organization_id': tenant.id, 'organization_role': 'member',
               'full_name': 'Updated name', 'expected_auth_version': target.auth_version}
    edited = client.put(f'/api/admin/users/{target.id}', headers=h, json=payload)
    assert edited.status_code == 200 and edited.json()['full_name'] == 'Updated name'
    assert client.put(f'/api/admin/users/{target.id}', headers=h, json=payload).status_code == 409
    assert client.get('/api/auth/me', headers=token).status_code == 401
    archived = client.post(f'/api/admin/users/{target.id}/archive', headers=h, json={'expected_auth_version': edited.json()['auth_version']})
    assert archived.status_code == 200 and not archived.json()['is_active']
    assert client.post(f'/api/admin/users/{owner.id}/archive', headers=h, json={'expected_auth_version': owner.auth_version}).status_code == 409
    tenant.seat_limit = 1
    db_session.commit()
    assert client.post(f'/api/admin/users/{target.id}/restore', headers=h, json={'expected_auth_version': archived.json()['auth_version']}).status_code == 409
    assert db_session.get(User, target.id).is_active is False


def test_user_creation_is_invitation_not_forged_account_or_consent(client, db_session, admin):
    tenant = org(db_session)
    result = client.post('/api/admin/users', headers=headers(admin), json={'email': 'invited@example.test', 'organization_id': tenant.id})
    assert result.status_code == 201
    assert result.json()['account_created'] is False and result.json()['resource_type'] == 'invitation'
    assert db_session.query(User).filter_by(email='invited@example.test').first() is None
    assert db_session.query(LegalReceipt).count() == 0


def test_invitation_reissue_revokes_old_link_changes_role_and_preserves_accepted_history(client, db_session, admin):
    tenant = org(db_session, seat_limit=1)
    old, old_token = invite(client, admin, tenant)
    response = client.post(f"/api/admin/invitations/{old['id']}/resend", headers=headers(admin), json={'role': 'candidate', 'locale': 'en'})
    assert response.status_code == 201, response.text
    assert response.json()['role'] == 'candidate' and response.json()['id'] != old['id']
    assert client.post('/api/auth/invitations/inspect', json={'token': old_token}).status_code == 410
    new_token = response.json()['url'].split('#token=')[1]
    assert client.post('/api/auth/invitations/accept', json=acceptance(new_token)).status_code == 201
    assert client.post(f"/api/admin/invitations/{response.json()['id']}/resend", headers=headers(admin), json={}).status_code == 409
    assert db_session.get(Invitation, old['id']).revoked_at is not None


def test_course_create_update_archive_preserve_snapshot_and_learner_access(client, db_session, admin, world):
    course, _, _, _, _, version = publish(client, db_session, world['author'])
    assignment = assign(client, world, course)
    h = headers(admin)
    original = client.get(f'/api/admin/courses/{course.id}', headers=h).json()
    changed = client.put(f'/api/admin/courses/{course.id}', headers=h, json={'title': 'New working name', 'description': 'New summary', 'expected_state': original['state_token']})
    assert changed.status_code == 200, changed.text
    assert changed.json()['publication_status'] == 'draft'
    assert db_session.get(CourseVersion, version.id).name == 'Onboarding'
    assert client.put(f'/api/admin/courses/{course.id}', headers=h, json={'title': 'Lost update', 'expected_state': original['state_token']}).status_code == 409
    archived = client.post(f'/api/admin/courses/{course.id}/archive', headers=h, json={'expected_state': changed.json()['state_token']})
    assert archived.status_code == 200, archived.text
    assert client.get(f"/api/learning/assignments/{assignment['id']}", headers=headers(world['learner'])).status_code == 200
    assert client.get('/api/admin/courses?archived=true', headers=h).json()['items'][0]['id'] == course.id
    assert client.get('/api/admin/courses', headers=h).json()['total'] == 0
    restored = client.post(f'/api/admin/courses/{course.id}/restore', headers=h, json={'expected_state': archived.json()['state_token']})
    assert restored.status_code == 200 and not restored.json()['is_deleted']
    # Operator access still cannot read private author content.
    assert client.get(f'/api/courses/{course.id}/structure', headers=h).status_code in (403, 404)
    created = client.post('/api/admin/courses', headers=h, json={'owner_id': world['author'].id, 'title': 'Operator-created draft'})
    assert created.status_code == 201 and created.json()['status'] == 'draft'
    assert db_session.get(Course, created.json()['id']).organization_id == world['org'].id


def test_course_archive_blocks_active_job_restore_does_not_cross_tenant_or_quota(client, db_session, admin, world):
    course = Course(name='Safe', owner_id=world['author'].id, status='ready')
    db_session.add(course); db_session.flush()
    run = GenerationRun(course_id=course.id, owner_id=world['author'].id, run_type='graph_generation', status='paused')
    db_session.add(run); db_session.commit()
    h = headers(admin)
    data = client.get(f'/api/admin/courses/{course.id}', headers=h).json()
    assert client.post(f'/api/admin/courses/{course.id}/archive', headers=h, json={'expected_state': data['state_token']}).status_code == 409
    run.status = 'failed'; run.cancel_requested = True; course.is_deleted = True
    world['author'].organization_id = world['other_org'].id
    db_session.commit()
    data = client.get(f'/api/admin/courses/{course.id}', headers=h).json()
    assert client.post(f'/api/admin/courses/{course.id}/restore', headers=h, json={'expected_state': data['state_token']}).status_code == 409
    assert db_session.get(Course, course.id).is_deleted


def test_request_workflow_preserves_original_and_append_only_notes(client, db_session, admin):
    h = headers(admin)
    original = {'email': 'incoming@example.test', 'name': 'Customer', 'message': 'Please help with an existing account.', 'purpose': 'support'}
    result = client.post('/api/admin/requests', headers=h, json=original)
    assert result.status_code == 201, result.text
    row = result.json(); path = '/api/admin/requests/' + row['reference']
    assert client.post(path+'/archive', headers=h, json={'expected_state': row['state_token']}).status_code == 409
    closed = client.put(path, headers=h, json={'status': 'closed', 'note': 'Answered through the existing support channel.', 'expected_state': row['state_token']})
    assert closed.status_code == 200 and len(closed.json()['notes']) == 1
    assert client.put(path, headers=h, json={'status': 'open', 'expected_state': row['state_token']}).status_code == 409
    archived = client.post(path+'/archive', headers=h, json={'expected_state': closed.json()['state_token']})
    assert archived.status_code == 200 and archived.json()['status'] == 'archived'
    reopened = client.put(path, headers=h, json={'status': 'open', 'expected_state': archived.json()['state_token']})
    assert reopened.status_code == 200 and reopened.json()['message'] == original['message']
    assert len(client.get(path, headers=h).json()['notes']) == 1
    assert db_session.query(LegalReceipt).count() == 0
    assert db_session.query(AdminEvent).filter_by(action='request.recorded_by_operator').count() == 1
    assert client.delete('/api/admin/audit/1', headers=h).status_code in (404, 405)


def test_assign_orphan_binds_tenant_and_cannot_transfer_published_history(client, db_session, admin, world):
    row = Course(name='Unassigned', status='draft')
    db_session.add(row); db_session.commit()
    assigned = client.post(f'/api/admin/courses/{row.id}/assign-owner', headers=headers(admin), json={'user_id': world['author'].id})
    assert assigned.status_code == 200, assigned.text
    db_session.refresh(row)
    assert row.owner_id == world['author'].id and row.organization_id == world['org'].id
    assert client.get(f'/api/courses/{row.id}/structure', headers=headers(world['author'])).status_code == 200
    historical = Course(name='Historical', status='ready')
    db_session.add(historical); db_session.flush()
    db_session.add(CourseVersion(course_id=historical.id, revision=1, publication_status='published', name='Immutable'))
    db_session.commit()
    denied = client.post(f'/api/admin/courses/{historical.id}/assign-owner', headers=headers(admin), json={'user_id': world['author'].id})
    assert denied.status_code == 409
    assert db_session.get(Course, historical.id).owner_id is None
    from app.models.module import Module
    from app.models.lesson import Lesson
    from app.models.theory import Theory
    manual = Course(name='Manual content', status='ready')
    db_session.add(manual); db_session.flush()
    module = Module(course_id=manual.id, title='Private module')
    db_session.add(module); db_session.flush()
    lesson = Lesson(module_id=module.id, title='Private lesson')
    db_session.add(lesson); db_session.flush()
    db_session.add(Theory(lesson_id=lesson.id, content='Private teacher-only content'))
    db_session.commit()
    assert client.post(f'/api/admin/courses/{manual.id}/assign-owner', headers=headers(admin), json={'user_id': world['author'].id}).status_code == 409
    assert db_session.get(Course, manual.id).owner_id is None


def test_admin_updates_require_explicit_concurrency_version(client, db_session, admin):
    tenant = org(db_session)
    user = member(db_session, tenant)
    stale_org = client.put(f'/api/admin/organizations/{tenant.id}', headers=headers(admin), json={'name': 'Unsafe stale draft'})
    stale_user = client.put(f'/api/admin/users/{user.id}', headers=headers(admin), json={'is_active': False})
    assert stale_org.status_code == stale_user.status_code == 409
    assert stale_org.json()['detail']['code'] == stale_user.json()['detail']['code'] == 'client_refresh_required'
    db_session.refresh(tenant); db_session.refresh(user)
    assert tenant.name == 'Organization' and user.is_active
