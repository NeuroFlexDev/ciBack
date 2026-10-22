from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.db import Base
from app.models.administration import Organization
from app.models.materials import ScopedAIUsage, LearnerAssistantMessage, OrganizationMaterialPolicy
from app.models.user import User
from app.services.ai_resources import usage_summary
from app.services.interactive_quota import monthly_requests
from app.services.scoped_ai import tracked_completion
from app.services.learner_assistant_service import send_message
from tests.test_learning_runtime import world, publish, assign


def response(*args, **kwargs):
    return {"text": "Example explanation", "model": "test", "usage": {"input_tokens": 4, "output_tokens": 3}}


def ask(db, user, **kwargs):
    return tracked_completion(db, user.id, None, "chat", [{"role": "user", "content": "Explain"}], invoke=response, max_tokens=50, **kwargs)


def test_transfer_never_moves_old_quota_and_new_requests_capture_new_tenant(db_session, world):
    for org in (world["org"], world["other_org"]):
        db_session.add(OrganizationMaterialPolicy(organization_id=org.id, chat_monthly_limit=1))
    db_session.commit()
    ask(db_session, world["author"])
    receipt = db_session.query(ScopedAIUsage).one()
    assert receipt.organization_id == world["org"].id and receipt.scope_status == "captured"
    world["author"].organization_id = world["other_org"].id
    db_session.commit()
    assert usage_summary(db_session, world["owner"].id)["interactive_requests_this_month"] == 1
    assert usage_summary(db_session, world["author"].id)["interactive_requests_this_month"] == 0
    with pytest.raises(HTTPException) as exhausted:
        ask(db_session, world["reviewer"])
    assert exhausted.value.status_code == 429
    db_session.rollback()
    ask(db_session, world["author"])
    receipts = db_session.query(ScopedAIUsage).order_by(ScopedAIUsage.id).all()
    assert [row.organization_id for row in receipts] == [world["org"].id, world["other_org"].id]
    assert usage_summary(db_session, world["author"].id)["interactive_requests_admission_total"] == 1


def test_unattributed_legacy_is_separate_private_and_conservative(db_session, world):
    db_session.add(OrganizationMaterialPolicy(organization_id=world["org"].id, chat_monthly_limit=2))
    for owner in (world["author"], world["author"], world["reviewer"]):
        db_session.add(ScopedAIUsage(owner_id=owner.id, operation="chat", status="completed"))
    db_session.commit()
    own = usage_summary(db_session, world["author"].id)
    admin = usage_summary(db_session, world["owner"].id)
    assert own["interactive_requests_this_month"] == admin["interactive_requests_this_month"] == 0
    assert own["interactive_requests_own_legacy_unattributed"] == 2
    assert own["interactive_requests_admission_total"] == 2
    assert admin["interactive_requests_own_legacy_unattributed"] == 0
    with pytest.raises(HTTPException) as exhausted:
        ask(db_session, world["author"])
    assert exhausted.value.detail["own_legacy_unattributed_requests"] == 2
    db_session.rollback()
    assert db_session.query(ScopedAIUsage).filter(ScopedAIUsage.organization_id.is_not(None)).count() == 0
    ask(db_session, world["owner"])


def test_personal_receipts_are_confirmed_without_becoming_another_persons_history(db_session, auth_user):
    other = User(email="another-person@example.test", password_hash="test")
    db_session.add(other)
    db_session.commit()
    ask(db_session, auth_user)
    row = db_session.query(ScopedAIUsage).one()
    assert row.organization_id is None and row.scope_status == "captured"
    assert monthly_requests(db_session, auth_user.id, None)["confirmed"] == 1
    assert monthly_requests(db_session, other.id, None)["confirmed"] == 0
    row.created_at = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    db_session.commit()
    assert monthly_requests(db_session, auth_user.id, None)["admission_total"] == 0


def test_learner_receipt_is_frozen_idempotent_and_counts_with_author_chat(client, db_session, world, monkeypatch):
    course, _, lesson, *_ = publish(client, db_session, world["author"], assessment=False, practice=False)
    assignment = assign(client, world, course)
    db_session.add(OrganizationMaterialPolicy(organization_id=world["org"].id, chat_monthly_limit=2))
    db_session.commit()
    calls = []
    def invoke(*args, **kwargs):
        calls.append(True)
        return {**response(), "text": json.dumps({"answer": "Lesson explanation", "cited_lesson_ids": [lesson.id]})}
    monkeypatch.setattr("app.ai.gateway.invoke_messages", invoke)
    first = send_message(db_session, world["learner"], assignment["id"], lesson.id, "Explain", "quota-learner-123")
    retry = send_message(db_session, world["learner"], assignment["id"], lesson.id, "Explain", "quota-learner-123")
    assert first.id == retry.id and calls == [True]
    assert first.organization_id == world["org"].id and first.scope_status == "captured"
    ask(db_session, world["author"])
    with pytest.raises(HTTPException) as exhausted:
        send_message(db_session, world["learner"], assignment["id"], lesson.id, "Another", "quota-learner-456")
    assert exhausted.value.status_code == 429
    db_session.rollback()
    world["learner"].organization_id = world["other_org"].id
    db_session.commit()
    assert monthly_requests(db_session, world["owner"].id, world["org"].id)["confirmed"] == 2
    assert monthly_requests(db_session, world["learner"].id, world["other_org"].id)["confirmed"] == 0
    assert db_session.query(LearnerAssistantMessage).one().organization_id == world["org"].id


def test_inactive_tenant_is_rejected_before_receipt_or_provider(db_session, world):
    world["org"].is_active = False
    db_session.commit()
    with pytest.raises(HTTPException) as denied:
        ask(db_session, world["author"])
    assert denied.value.status_code == 403
    assert db_session.query(ScopedAIUsage).count() == 0


def test_membership_change_between_receipt_and_provider_cannot_charge_new_tenant(db_session, world, monkeypatch):
    from app.models.ai_resources import AIResourceUsage
    from app.services import ai_resources
    complete = ai_resources.completion
    calls = []
    def transfer_before_reservation(db, owner_id, course_id, role, messages, **kwargs):
        world["author"].organization_id = world["other_org"].id
        db.commit()
        kwargs["invoke"] = lambda *args, **kw: calls.append(True)
        return complete(db, owner_id, course_id, role, messages, **kwargs)
    monkeypatch.setattr(ai_resources, "completion", transfer_before_reservation)
    with pytest.raises(HTTPException) as denied:
        ask(db_session, world["author"])
    assert denied.value.status_code == 403 and calls == []
    assert db_session.query(AIResourceUsage).count() == 0
    receipt = db_session.query(ScopedAIUsage).one()
    assert receipt.organization_id == world["org"].id and receipt.status == "unavailable"
    assert receipt.input_tokens == receipt.output_tokens == 0


def test_real_concurrent_users_cannot_overbook_monthly_request_limit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'monthly-requests.sqlite'}", connect_args={"timeout": 15})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        organization = Organization(name="Concurrent organization")
        db.add(organization); db.flush()
        db.add(OrganizationMaterialPolicy(organization_id=organization.id, chat_monthly_limit=1))
        people = [User(email=f"requester-{i}@example.test", password_hash="test", organization_id=organization.id) for i in range(8)]
        db.add_all(people); db.commit()
        user_ids = [user.id for user in people]
    def compete(user_id):
        with factory() as db:
            try:
                ask(db, db.get(User, user_id))
                return True
            except HTTPException as error:
                assert error.status_code == 429
                db.rollback()
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(compete, user_ids)) == 1
    with factory() as db:
        assert db.query(ScopedAIUsage).count() == 1
    engine.dispose()


def test_migration_backfills_only_proven_course_or_assignment_and_can_downgrade(tmp_path):
    path = Path(__file__).parents[1] / "migrations/versions/ai_0029_interactive_receipt_scope.py"
    spec = importlib.util.spec_from_file_location("receipt_scope_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-receipts.sqlite'}")
    with engine.begin() as connection:
        # This deliberately includes transferred users. Migration must not read
        # their current membership to manufacture historical tenant ownership.
        for sql in (
            "CREATE TABLE organizations (id INTEGER PRIMARY KEY)",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, organization_id INTEGER)",
            "CREATE TABLE courses (id INTEGER PRIMARY KEY, organization_id INTEGER)",
            "CREATE TABLE learning_assignments (id INTEGER PRIMARY KEY, organization_id INTEGER, user_id INTEGER, course_version_id INTEGER)",
            "CREATE TABLE scoped_ai_usage (id INTEGER PRIMARY KEY, owner_id INTEGER, course_id INTEGER, created_at DATETIME)",
            "CREATE TABLE learner_assistant_messages (id INTEGER PRIMARY KEY, owner_id INTEGER, assignment_id INTEGER, course_version_id INTEGER, created_at DATETIME)",
            "INSERT INTO organizations VALUES (1), (2)",
            "INSERT INTO users VALUES (10,2)",
            "INSERT INTO courses VALUES (100,1), (200,NULL)",
            "INSERT INTO learning_assignments VALUES (1,1,10,7), (2,1,20,7)",
            "INSERT INTO scoped_ai_usage VALUES (1,10,100,CURRENT_TIMESTAMP), (2,10,NULL,CURRENT_TIMESTAMP), (3,10,200,CURRENT_TIMESTAMP)",
            "INSERT INTO learner_assistant_messages VALUES (1,10,1,7,CURRENT_TIMESTAMP), (2,10,999,7,CURRENT_TIMESTAMP), (3,10,2,7,CURRENT_TIMESTAMP), (4,10,1,999,CURRENT_TIMESTAMP)",
        ):
            connection.execute(text(sql))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(text("SELECT organization_id, scope_status FROM scoped_ai_usage ORDER BY id")).all() == [(1, "resolved"), (None, "legacy_unknown"), (None, "resolved")]
        assert connection.execute(text("SELECT organization_id, scope_status FROM learner_assistant_messages ORDER BY id")).all() == [(1, "resolved"), (None, "legacy_unknown"), (None, "legacy_unknown"), (None, "legacy_unknown")]
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(text("SELECT count(*) FROM scoped_ai_usage")).scalar_one() == 3
        assert connection.execute(text("SELECT count(*) FROM learner_assistant_messages")).scalar_one() == 4
    engine.dispose()
