"""Real row-lock regression; CI opts in via its disposable PostgreSQL service.

Uses an isolated random schema, never the application's/public tables. A regular
SQLite run skips this file: SQLite cannot prove PostgreSQL locking behaviour.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
from threading import Event, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database.db import Base
from app.models.administration import Organization
from app.models.learning import OrganizationOwnershipTransfer
from app.models.user import User
from app.routes import administration, learning
from app.services import organization_access as access


@pytest.fixture
def pg_sessions():
    dsn = os.environ.get('TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Set TEST_POSTGRES_DSN to a disposable PostgreSQL test service')
    if make_url(dsn).get_backend_name() != 'postgresql':
        pytest.fail('TEST_POSTGRES_DSN must select PostgreSQL')
    engine = create_engine(dsn)
    schema = 'identity_locks_' + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine.dispose()

    @event.listens_for(engine, 'connect')
    def isolated_connection(connection, _):
        # Session settings must survive a rollback and every pool checkout.
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
            cursor.execute("SET lock_timeout TO '4000ms'")
            cursor.execute("SET statement_timeout TO '6000ms'")
        connection.autocommit = False
    try:
        # Base.metadata creates the ORM's native VECTOR column directly, so the
        # isolated schema needs pgvector before create_all (migrations normally
        # install it first in a deployed database).
        with engine.begin() as connection:
            connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_ownership_accept_and_member_change_do_not_deadlock(pg_sessions, monkeypatch):
    with pg_sessions() as db:
        org = Organization(name='Concurrent ownership test')
        db.add(org); db.flush()
        old = User(email='old@test.invalid', password_hash='unused', organization_id=org.id, organization_role='owner')
        new = User(email='new@test.invalid', password_hash='unused', organization_id=org.id, organization_role='admin')
        operator = User(email='operator@test.invalid', password_hash='unused', is_admin=True)
        db.add_all([old, new, operator]); db.flush()
        transfer = OrganizationOwnershipTransfer(organization_id=org.id, from_user_id=old.id, to_user_id=new.id,
            expires_at=datetime.utcnow()+timedelta(days=1))
        db.add(transfer); db.commit()
        org_id, old_id, new_id, operator_id, transfer_id = org.id, old.id, new.id, operator.id, transfer.id

    org_held, change_at_org, proceed = Event(), Event(), Event()
    original_lock = access.lock_org

    def coordinated_lock(db, org_id):
        name = current_thread().name
        if name.startswith('member-change'):
            # In the old code this happens AFTER acquiring the target User row;
            # the transfer already holds its Organization row: deadlock cycle.
            change_at_org.set()
        result = original_lock(db, org_id)
        if name.startswith('ownership-accept'):
            org_held.set()
            assert proceed.wait(3), 'Member change did not reach organization lock'
        return result
    monkeypatch.setattr(access, 'lock_org', coordinated_lock)
    monkeypatch.setattr(administration, 'lock_org', coordinated_lock)
    monkeypatch.setattr(learning, 'lock_org', coordinated_lock)

    def accept():
        with pg_sessions() as db:
            return learning.accept_transfer(transfer_id, db, db.get(User, new_id))

    def change():
        with pg_sessions() as db:
            return administration.change_user(db, db.get(User, operator_id), db.get(User, old_id),
                administration.UserChange(is_active=True, organization_id=org_id, organization_role='member'))

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='ownership-accept') as first, \
         ThreadPoolExecutor(max_workers=1, thread_name_prefix='member-change') as second:
        accepted = first.submit(accept)
        try:
            assert org_held.wait(3), 'Transfer did not acquire organization lock'
            changed = second.submit(change)
            assert change_at_org.wait(3), 'Member change did not attempt organization lock'
        finally:
            proceed.set()
        assert accepted.result(timeout=8)['reauthentication_required']
        assert changed.result(timeout=8)['organization_role'] == 'member'

    with pg_sessions() as db:
        assert db.get(User, new_id).organization_role == 'owner'
        assert db.get(User, old_id).auth_version == 2  # Transfer + subsequent role change.
        assert db.get(User, new_id).auth_version == 1
        assert db.get(OrganizationOwnershipTransfer, transfer_id).accepted_at is not None
