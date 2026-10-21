import pytest
from fastapi import HTTPException
from sqlalchemy import event, update
from app.models.user import User
from app.services import organization_access as access
from tests.test_learning_runtime import world


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
