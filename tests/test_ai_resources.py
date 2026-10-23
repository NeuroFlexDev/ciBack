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


def test_operator_policy_changes_only_selected_organization(client, db_session, auth_user, auth_headers):
    first, second = Organization(name="Low budget"), Organization(name="Default budget")
    db_session.add_all([first, second]); db_session.flush()
    auth_user.is_admin, auth_user.organization_id = True, first.id
    db_session.commit()
    payload = {"disk_bytes_limit": 100000, "chat_monthly_limit": 30, "image_monthly_limit": 2,
        "resource_limits": {"queued_limit": 2, "background_limit": 1, "interactive_limit": 1,
            "monthly_tokens": 10, "monthly_ocr_pages": 3, "monthly_rub": "0.5"}}
    payload["expected_revision"] = client.get(f"/api/admin/organizations/{first.id}/material-policy", headers=auth_headers).json()["revision"]
    response = client.put(f"/api/admin/organizations/{first.id}/material-policy", headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["resource_limits"]["monthly_rub"] == "0.5"
    assert client.put(f"/api/admin/organizations/{first.id}/material-policy", headers=auth_headers, json=payload).status_code == 409
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation="chat", tokens=11)
    db_session.rollback()
    auth_user.organization_id = second.id
    db_session.commit()
    resources.reserve(db_session, owner_id=auth_user.id, operation="chat", tokens=11)
    db_session.commit()
    assert resources.usage_summary(db_session, auth_user.id)["tokens_limit"] == settings.AI_ORG_MONTHLY_TOKENS
    auth_user.organization_id, auth_user.is_admin = first.id, False
    db_session.commit()
    response = client.get("/api/materials/resource-usage", headers=auth_headers)
    assert response.status_code == 200 and response.json()["tokens_limit"] == 10
    assert client.put(f"/api/admin/organizations/{first.id}/material-policy", headers=auth_headers, json=payload).status_code == 403


def test_empty_compose_prices_are_unconfigured_not_free():
    from app.core.config import Settings
    config = Settings(_env_file=None, DATABASE_URL="sqlite:///:memory:", JWT_SECRET="test-only-config-secret-32-characters", IMAGE_PRICE_RUB="", EMBEDDING_RUB_PER_MILLION="   ")
    assert config.IMAGE_PRICE_RUB is None and config.EMBEDDING_RUB_PER_MILLION is None


def test_partial_ocr_reservation_never_exceeds_remaining_allowance(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(settings, "AI_ORG_MONTHLY_OCR_PAGES", 3)
    first = resources.reserve(db_session, owner_id=auth_user.id, operation="document", pages=30, partial_ocr=True)
    db_session.commit()
    second = resources.reserve(db_session, owner_id=auth_user.id, operation="document", pages=30, partial_ocr=True)
    db_session.commit()
    assert (first.ocr_pages, second.ocr_pages) == (3, 0)
    resources.finish(db_session, first, pages=1)
    third = resources.reserve(db_session, owner_id=auth_user.id, operation="document", pages=30, partial_ocr=True)
    assert third.ocr_pages == 2


def test_text_pdf_works_without_ocr_allowance_and_scan_fails_before_ocr(monkeypatch):
    import fitz
    from io import BytesIO
    from PIL import Image
    from app.services.document_safety import bounded_extract_blocks
    monkeypatch.setattr(settings, "AI_OCR_ENABLED", True)
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 30), "Ordinary selectable text")
        blocks, usage = bounded_extract_blocks(pdf.tobytes(), "application/pdf", with_usage=True, ocr_page_limit=0)
    assert any("Ordinary selectable text" in block.text for block in blocks)
    assert usage["ocr_pages"] == 0
    calls = []
    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", lambda *args, **kwargs: calls.append(True))
    raster = BytesIO()
    Image.new("RGB", (50, 50), "white").save(raster, "PNG")
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_image(page.rect, stream=raster.getvalue())
        with pytest.raises(ValueError, match="квоты OCR"):
            bounded_extract_blocks(pdf.tobytes(), "application/pdf", ocr_page_limit=0)
    assert calls == []



def test_deployment_suspension_drains_background_without_stopping_current_ai(db_session, auth_user, monkeypatch):
    monkeypatch.setattr(resources, 'background_admission_paused', lambda: True)
    queued = resources.reserve(db_session, owner_id=auth_user.id, operation='generation', lane='background', queued=True)
    db_session.commit()
    with pytest.raises(resources.CapacityBusy) as error:
        resources.claim(db_session, queued.key)
    assert error.value.detail['code'] == 'ai_deployment_pending'
    db_session.rollback()
    with pytest.raises(resources.CapacityBusy):
        resources.reserve(db_session, owner_id=auth_user.id, operation='document', lane='background')
    db_session.rollback()
    # Calls inside an admitted generation and interactive help remain available.
    resources.reserve(db_session, owner_id=auth_user.id, operation='lesson_writer')
    resources.reserve(db_session, owner_id=auth_user.id, operation='chat', lane='interactive')
    db_session.commit()
    monkeypatch.setattr(resources, 'background_admission_paused', lambda: False)
    assert resources.claim(db_session, queued.key)
