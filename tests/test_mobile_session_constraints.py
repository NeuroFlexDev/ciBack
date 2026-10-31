"""Exercise native login with real foreign-key enforcement, including PostgreSQL in CI."""
import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker

from app.database.db import Base
from app.core.security import hash_password
from app.models.user import User
from app.models.integrations import MobileSession, MobileRefreshToken
from app.routes.mobile_auth import LoginInput, RefreshInput, login, refresh
from app.services.mobile_sessions import issue
from tests.test_identity_locks_postgres import pg_sessions  # noqa: F401


@pytest.fixture(params=['sqlite', 'postgres'])
def constrained_sessions(request):
    if request.param == 'postgres':
        yield request.getfixturevalue('pg_sessions')
        return
    engine = create_engine('sqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enable_constraints(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def test_login_refresh_and_failed_issue_are_atomic_with_constraints(constrained_sessions):
    with constrained_sessions() as db:
        user = User(email='mobile-constraints@test.invalid', password_hash=hash_password('password123'))
        db.add(user)
        db.commit()
        tokens = login(LoginInput(email=user.email, password='password123', device_name='Constraint test'), db)
        assert db.scalar(select(func.count()).select_from(MobileSession)) == 1
        assert db.scalar(select(func.count()).select_from(MobileRefreshToken)) == 1
        rotated = refresh(RefreshInput(refresh_token=tokens['refresh_token']), db)
        assert rotated['refresh_token'] != tokens['refresh_token']
        assert db.scalar(select(func.count()).select_from(MobileSession)) == 1
        assert db.scalar(select(func.count()).select_from(MobileRefreshToken)) == 2
        assert db.scalar(select(func.count()).select_from(MobileRefreshToken)
            .where(MobileRefreshToken.consumed_at.is_not(None))) == 1
        # Issuance must not commit independently: a later failure rolls back both rows.
        issue(db, user, device_name='Rolled back')
        db.rollback()
        assert db.scalar(select(func.count()).select_from(MobileSession)) == 1
        assert db.scalar(select(func.count()).select_from(MobileRefreshToken)) == 2
