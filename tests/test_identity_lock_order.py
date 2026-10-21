import pytest
from fastapi import HTTPException
from sqlalchemy import event, update
from app.models.user import User
from app.services import organization_access as access
from tests.test_learning_runtime import world
from app.routes import administration, learning
from app.models.administration import AdminEvent, Invitation
from app.models.learning import OrganizationOwnershipTransfer
from app.schemas.learning import OwnerTransferInput


def test_member_locks_organizations_before_user_and_returns_fresh_identity(db_session, world):
    locked = []
    def observe(state):
        statement = state.statement
        if getattr(statement, '_for_update_arg', None) is not None:
            locked.append((statement.column_descriptions[0]['entity'].__tablename__, state.parameters))
    event.listen(db_session, 'do_orm_execute', observe)
    try:
        user = access.lock_member(db_session, world['owner'].id, extra_org_ids=[world['other_org'].id,world['org'].id])
    finally:
        event.remove(db_session, 'do_orm_execute', observe)
    assert [table for table, _ in locked] == ['organizations', 'organizations', 'users']
    assert user.id == world['owner'].id


def test_membership_change_during_lock_wait_never_locks_new_org_after_user(db_session, world, monkeypatch):
    original = access.lock_org
    calls = []
    def change_after_probe(db, org_id):
        calls.append(org_id)
        org = original(db, org_id)
        db.execute(update(User).where(User.id==world['owner'].id).values(organization_id=world['other_org'].id).execution_options(synchronize_session=False))
        return org
    monkeypatch.setattr(access, 'lock_org', change_after_probe)
    with pytest.raises(HTTPException) as error:
        access.lock_member(db_session, world['owner'].id)
    assert error.value.status_code == 409
    assert calls == [world['org'].id]


def test_inactive_management_target_and_revoked_actor_are_distinct(db_session, world):
    actor = world['owner']
    actor.is_active = False; actor.auth_version += 1; db_session.commit()
    assert access.lock_member(db_session,actor.id,allow_inactive=True).id == actor.id
    with pytest.raises(HTTPException) as error:
        access.lock_member(db_session,actor.id,expected_auth_version=0)
    assert error.value.status_code == 401


def test_user_locks_have_stable_order_for_cross_org_move(db_session, world):
    actor = User(email='operator-lock-order@example.test', password_hash='unused', is_admin=True)
    db_session.add(actor); db_session.commit()
    target = world['author']
    locked = []
    def observe(state):
        statement = state.statement
        if getattr(statement, '_for_update_arg', None) is not None:
            locked.append((statement.column_descriptions[0]['entity'].__tablename__, list(statement.compile().params.values())[0]))
    event.listen(db_session, 'do_orm_execute', observe)
    try:
        administration.change_user(db_session, actor, target, administration.UserChange(
            is_active=True, organization_id=world['other_org'].id, organization_role='member'))
    finally:
        event.remove(db_session, 'do_orm_execute', observe)
    assert locked == [('organizations', world['org'].id), ('organizations', world['other_org'].id),
                      ('users', target.id), ('users', actor.id)]
    assert target.organization_id == world['other_org'].id and target.auth_version == 1


def revoke_after_org_lock(monkeypatch, user_id):
    original = access.lock_org
    changed = False
    def revoke(db, org_id):
        nonlocal changed
        org = original(db, org_id)
        if not changed:
            changed = True
            db.execute(update(User).where(User.id == user_id).values(auth_version=User.auth_version+1).execution_options(synchronize_session=False))
        return org
    monkeypatch.setattr(access, 'lock_org', revoke)


@pytest.mark.parametrize('operation', ['change', 'invite', 'transfer', 'grant'])
def test_management_rechecks_actor_session_after_wait(db_session, world, monkeypatch, operation):
    owner = world['owner']
    revoke_after_org_lock(monkeypatch, owner.id)
    with pytest.raises(HTTPException) as error:
        if operation == 'change':
            administration.change_user(db_session, owner, world['learner'], administration.UserChange(
                is_active=False, organization_id=world['org'].id))
        elif operation == 'invite':
            administration.make_invitation(db_session, owner, administration.InviteInput(
                email='new-lock@example.test', organization_id=world['org'].id))
        elif operation == 'transfer':
            learning.start_transfer(OwnerTransferInput(user_id=world['admin'].id), db_session, owner)
        else:
            learning.grant_role(world['learner'].id, learning.RoleInput(role='author', scope_type='organization', scope_id=0), db_session, owner)
    assert error.value.status_code == 401
    assert world['learner'].is_active
    assert db_session.query(Invitation).count() == 0
    assert db_session.query(OrganizationOwnershipTransfer).count() == 0
    assert db_session.query(AdminEvent).count() == 0


def test_transfer_cancellation_during_wait_is_not_overwritten(db_session, world, monkeypatch):
    result = learning.start_transfer(OwnerTransferInput(user_id=world['admin'].id), db_session, world['owner'])
    original = access.lock_org
    def cancel(db, org_id):
        from datetime import datetime
        org = original(db, org_id)
        db.execute(update(OrganizationOwnershipTransfer).where(OrganizationOwnershipTransfer.id == result['id'])
            .values(cancelled_at=datetime.utcnow()).execution_options(synchronize_session=False))
        return org
    monkeypatch.setattr(access, 'lock_org', cancel)
    with pytest.raises(HTTPException) as error:
        learning.accept_transfer(result['id'], db_session, world['admin'])
    assert error.value.status_code == 410
    assert world['owner'].organization_role == 'owner'
    assert world['admin'].organization_role == 'admin'
    assert world['owner'].auth_version == world['admin'].auth_version == 0


@pytest.mark.parametrize('operation', ['image', 'generation'])
def test_ai_admission_rejects_session_revoked_during_lock_wait(db_session, world, monkeypatch, operation):
    from app.services import image_service
    from app.routes.pipeline import launch_generation
    from app.schemas.generation_run import GenerationRunCreate
    from tests.test_async_generation_launch import _settings
    revoke_after_org_lock(monkeypatch, world['author'].id)
    with pytest.raises(HTTPException) as error:
        if operation == 'image':
            image_service.prepare_generation(db_session, world['author'], 999, 'No provider call', 'request-before-revoke')
        else:
            launch_generation(999, GenerationRunCreate(settings=_settings(), document_ids=[1]), db_session, world['author'])
    assert error.value.status_code == 401
