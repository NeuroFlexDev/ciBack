from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.db import Base
from app.core.config import settings, AIProvider
from app.models.user import User
from app.models.administration import Organization
from app.models.ai_resources import AIResourceUsage
from app.services import ai_resources as resources
from tests.factories import make_course


def test_background_cannot_consume_interactive_pool(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(settings, "AI_ORG_BACKGROUND_LIMIT", 1)
    monkeypatch.setattr(settings, "AI_ORG_INTERACTIVE_LIMIT", 1)
    resources.reserve(db_session, owner_id=auth_user.id, operation="document", lane="background")
    db_session.commit()
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation="generation", lane="background")
    db_session.rollback()
    chat = resources.reserve(db_session, owner_id=auth_user.id, operation="chat", lane="interactive")
    assert chat.status == "running"
    resources.finish(db_session, chat)


def test_queued_and_running_admission_are_atomic_and_idempotent(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(settings, "AI_ORG_QUEUED_LIMIT", 1)
    first = resources.reserve(db_session, owner_id=auth_user.id, operation="generation", lane="background", key="test:first", queued=True)
    db_session.commit()
    assert resources.reserve(db_session, owner_id=auth_user.id, operation="generation", lane="background", key="test:first", queued=True).id == first.id
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation="generation", lane="background", key="test:second", queued=True)
    db_session.rollback()
    assert resources.claim(db_session, "test:first")
    assert not resources.claim(db_session, "test:first")


def test_org_and_install_budgets_include_unknown_usage(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(settings, "AI_ORG_MONTHLY_RUB", Decimal("1"))
    first = resources.reserve(db_session, owner_id=auth_user.id, operation="embedding", cost=Decimal("0.7"), tokens=100)
    db_session.commit()
    resources.finish(db_session, first, failed=True)
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation="chat", cost=Decimal("0.4"))
    db_session.rollback()
    assert first.status == "unknown" and first.cost_rub == Decimal("0.700000")
    resources.finish(db_session, first, unsent=True)
    resources.reserve(db_session, owner_id=auth_user.id, operation="chat", cost=Decimal("0.9"))
    db_session.commit()
    monkeypatch.setattr(settings, "AI_INSTALL_MONTHLY_TOKENS", 5)
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation="embedding", tokens=6)


def test_ocr_pages_and_expired_permits_retain_spend(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(settings, "AI_ORG_MONTHLY_OCR_PAGES", 30)
    record = resources.reserve(db_session, owner_id=auth_user.id, operation="document", lane="background", pages=30)
    record.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    resources.locked(db_session)
    db_session.commit()
    assert record.status == "unknown"
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation="document", pages=1)
    db_session.rollback()
    assert record.ocr_pages == 30


def test_ledger_keeps_original_org_after_employee_transfer(db_session, auth_user):
    first = Organization(name="Original")
    second = Organization(name="Destination")
    db_session.add_all([first, second]); db_session.flush()
    auth_user.organization_id = first.id
    db_session.commit()
    record = resources.reserve(db_session, owner_id=auth_user.id, operation="chat", tokens=200)
    db_session.commit()
    auth_user.organization_id = second.id
    db_session.commit()
    assert record.organization_id == first.id
    assert resources.usage_summary(db_session, auth_user.id)["tokens_reserved_or_used"] == 0


def test_remote_embedding_needs_explicit_price_and_never_sends_without_budget(db_session, auth_user, monkeypatch):
    from app.ai import retrieval
    monkeypatch.setattr(settings, "AI_PROVIDERS", [AIProvider(id="embed", base_url="https://example.invalid/v1")])
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "embed")
    monkeypatch.setattr("app.ai.providers.available", lambda _: True)
    monkeypatch.setattr(settings, "EMBEDDING_RUB_PER_MILLION", None)
    calls = []
    monkeypatch.setattr(retrieval, "embedding_client", lambda: calls.append(True))
    with pytest.raises(ValueError, match="price"):
        retrieval.metered_embedding(db_session, auth_user.id, None, ["example"])
    assert calls == []
    monkeypatch.setattr(settings, "EMBEDDING_RUB_PER_MILLION", Decimal("100"))
    monkeypatch.setattr(settings, "AI_ORG_MONTHLY_TOKENS", 1)
    with pytest.raises(resources.CapacityBusy):
        retrieval.metered_embedding(db_session, auth_user.id, None, ["example"])
    assert calls == []


def test_real_concurrent_connections_cannot_overbook_installation(tmp_path, monkeypatch):
    # Uses distinct SQLite connections/transactions, not a mocked lock or a
    # shared pytest savepoint. PostgreSQL uses the same singleton UPDATE lock.
    engine = create_engine(f"sqlite:///{tmp_path / 'admission.sqlite'}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        user = User(email="concurrent@example.com", password_hash="test")
        db.add(user); db.commit(); owner_id = user.id
    monkeypatch.setattr(settings, "AI_INSTALL_INTERACTIVE_LIMIT", 1)
    def compete(number):
        with factory() as db:
            try:
                resources.reserve(db, owner_id=owner_id, operation="chat", lane="interactive", key=f"race:{number}")
                db.commit()
                return True
            except resources.CapacityBusy:
                db.rollback()
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(compete, range(8))) == 1
    engine.dispose()


def test_transport_timeout_is_not_retried_and_reservation_is_retained(db_session, auth_user):
    calls = []
    def unknown(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("lost acknowledgement")
    with pytest.raises(TimeoutError):
        resources.completion(db_session, auth_user.id, None, "chat", [{"role": "user", "content": "Hi"}], invoke=unknown, max_tokens=50)
    assert calls == [True]
    row = db_session.query(AIResourceUsage).one()
    assert row.status == "unknown" and row.tokens > 50 and row.cost_rub > 0

