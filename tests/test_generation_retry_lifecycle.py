"""A status poll must not expire a retry using its previous attempt's lease."""
import copy
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.core.config import settings
from app.models.agent_artifact import AgentArtifact
from app.models.ai_resources import AIResourceUsage
from app.models.ai_state import AICall
from app.services import ai_resources as resources
from app.services.pipeline_service import PipelineService
from tests.factories import make_course
from tests.test_generation_status import _run


def _failed_attempt(db, user, *, admission_status="unknown", expired=True):
    course = make_course(db, owner_id=user.id)
    old = datetime.utcnow() - timedelta(hours=2)
    run = _run(db, user, course, status="failed", retryable=True,
        current_stage="lesson_writing", progress_percent=50,
        queued_at=old, started_at=old, finished_at=old + timedelta(minutes=5),
        settings_snapshot={"lesson_count": 6, "language": "ru"}, error_code="ai_invalid_response")
    lease = AIResourceUsage(key=f"generation:{run.id}", run_id=run.id, owner_id=user.id,
        course_id=course.id, operation="generation", lane="background", status=admission_status,
        expires_at=(old + timedelta(hours=1) if expired else datetime.utcnow() + timedelta(minutes=5)))
    evidence = AgentArtifact(run_id=run.id, course_id=course.id, agent="ingestion", artifact="document_knowledge",
        sequence=0, status="completed", input_fingerprint="frozen-evidence", payload={"knowledge_items": [{"id": "kn:1", "statement": "Сохранённый факт"}]})
    call = AICall(run_id=run.id, agent="ingestion", model="test", status="completed", input_tokens=125, output_tokens=134)
    paid = AIResourceUsage(key=f"paid:{run.id}", run_id=run.id, owner_id=user.id, course_id=course.id,
        operation="lesson_writer", lane="metered", status="unknown", tokens=259, cost_rub=Decimal("2.50"))
    db.add_all([lease, evidence, call, paid]); db.commit()
    return course, run, lease, evidence, call, paid


def test_retry_rearms_lease_before_first_poll_preserving_checkpoint_and_spend(client, db_session, auth_user, auth_headers, monkeypatch):
    course, run, lease, evidence, call, paid = _failed_attempt(db_session, auth_user)
    before = (copy.deepcopy(run.settings_snapshot), copy.deepcopy(evidence.payload), evidence.id, call.id, paid.id)
    enqueued = []
    monkeypatch.setattr("app.routes.pipeline.enqueue_generation", enqueued.append)

    response = client.post(f"/api/generation-runs/{run.id}/retry", headers=auth_headers)
    assert response.status_code == 202, response.text
    assert enqueued == [run.id]
    assert (run.status, run.attempt, run.started_at, run.finished_at, run.cancel_requested) == ("queued", 2, None, None, False)
    assert (lease.status, lease.expires_at, lease.finished_at) == ("queued", None, None)
    # The browser polls before an idle worker dequeues (the real production race).
    polled = client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).json()
    assert polled["status"] == "queued" and polled["status_error"] is None and not run.cancel_requested
    assert resources.claim(db_session, lease.key)
    assert lease.expires_at > datetime.utcnow()
    polled = client.get(f"/api/generation-runs/{run.id}", headers=auth_headers).json()
    assert polled["status"] == "queued" and not run.cancel_requested
    assert (run.settings_snapshot, evidence.payload, evidence.id, call.id, paid.id) == before
    assert (paid.status, paid.tokens, paid.cost_rub) == ("unknown", 259, Decimal("2.50"))
    assert (call.input_tokens, call.output_tokens) == (125, 134)
    assert db_session.query(AICall).count() == 1
    assert course.status == "generating"


def test_retry_waits_for_previous_worker_to_release_live_slot(db_session, auth_user):
    _, run, lease, *_ = _failed_attempt(db_session, auth_user, admission_status="running", expired=False)
    with pytest.raises(HTTPException) as error:
        PipelineService.retry_graph_generation(db_session, run.id, auth_user.id)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "generation_worker_finishing"
    assert (run.status, run.attempt, lease.status) == ("failed", 1, "running")


def test_retry_still_obeys_queue_capacity_without_spending_or_advancing_attempt(db_session, auth_user, monkeypatch):
    _, run, lease, *_ = _failed_attempt(db_session, auth_user)
    monkeypatch.setattr(settings, "AI_ORG_QUEUED_LIMIT", 0)
    with pytest.raises(resources.CapacityBusy):
        PipelineService.retry_graph_generation(db_session, run.id, auth_user.id)
    assert (run.status, run.attempt, lease.status) == ("failed", 1, "unknown")


@pytest.mark.parametrize("lease_status", ["running", "unknown"])
def test_expired_current_attempt_still_pauses_and_retains_checkpoint(db_session, auth_user, lease_status):
    _, run, lease, evidence, *_ = _failed_attempt(db_session, auth_user, admission_status=lease_status)
    run.status = "running"
    db_session.commit()
    resources.reconcile_expired_run(db_session, run)
    assert (run.status, run.cancel_requested, run.error_code) == ("paused", True, "worker_result_unknown")
    assert evidence.status == "completed"


def test_historical_expiry_cannot_cancel_new_attempt_even_before_worker_claim(db_session, auth_user):
    _, run, lease, *_ = _failed_attempt(db_session, auth_user)
    run.status, run.queued_at, run.started_at = "queued", datetime.utcnow(), None
    db_session.commit()
    resources.reconcile_expired_run(db_session, run)
    assert run.status == "queued" and not run.cancel_requested


def test_poll_refreshes_lease_after_waiting_for_course_lock(db_session, auth_user, monkeypatch):
    _, run, lease, *_ = _failed_attempt(db_session, auth_user, admission_status="running")
    run.status = "running"
    db_session.commit()
    refresh = db_session.refresh
    future = datetime.utcnow() + timedelta(minutes=5)
    interleaved = []

    def claim_while_poll_waits(instance, *args, **kwargs):
        if instance is run and not interleaved:
            interleaved.append(True)
            # Emulate a worker's committed lease renewal before the poll gets
            # its course lock, retaining the poll's stale ORM identity map.
            db_session.execute(update(AIResourceUsage).where(AIResourceUsage.id == lease.id)
                .values(expires_at=future).execution_options(synchronize_session=False))
        return refresh(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "refresh", claim_while_poll_waits)
    resources.reconcile_expired_run(db_session, run)
    assert interleaved and lease.expires_at == future
    assert run.status == "running" and not run.cancel_requested


@pytest.mark.parametrize(("code", "notice_fragment"), [
    ("worker_result_unknown", "Рабочий процесс не подтвердил завершение"),
    ("time_limit", "Достигнут лимит времени генерации"),
    ("user_cancelled", "Генерация остановлена. Завершённые этапы сохранены"),
])
def test_paused_status_distinguishes_safe_system_reason_from_manual_stop(client, db_session, auth_user, auth_headers, code, notice_fragment):
    course = make_course(db_session, owner_id=auth_user.id)
    run = _run(db_session, auth_user, course, status="paused", retryable=True, cancel_requested=True,
        error_code=code, error_message="private exception text must not be exposed")
    response = client.get(f"/api/generation-runs/{run.id}", headers=auth_headers)
    assert response.status_code == 200
    assert notice_fragment in response.json()["status_notice"]
    assert "private exception" not in response.text
