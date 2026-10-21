from datetime import datetime, timedelta
import pytest
from app.models.agent_artifact import AgentArtifact
from app.models.ai_resources import AIThreadReference
from app.models.ai_state import AIResponseCache
from app.models.chat import Chat, ChatMessage
from app.models.course_version import CourseVersion
from app.models.generation_run import GenerationRun
from app.models.learning import PublishedLearningContent, LearningAssignment, LearningResponse, LearningGradeEvent
from app.models.administration import Organization
from app.models.module_version import ModuleVersion
from app.models.lesson_version import LessonVersion
from app.services.ai_retention import RetentionPolicy, retention_plan, apply_retention
from tests.factories import make_course

NOW = datetime(2026, 10, 22, 12)
OLD = NOW - timedelta(days=120)


def run_artifact(db, user, course, *, status="completed", retryable=False):
    run = GenerationRun(owner_id=user.id, course_id=course.id, run_type="graph_generation", status=status,
        created_at=OLD, finished_at=OLD, retryable=retryable)
    db.add(run); db.flush()
    artifact = AgentArtifact(run_id=run.id, course_id=course.id, agent="writer", artifact="lesson", sequence=0,
        payload={"private": "old source"}, created_at=OLD)
    thread = AIThreadReference(thread_id=f"{run.id:064x}", run_id=run.id, created_at=OLD)
    db.add_all([artifact, thread]); db.commit()
    return run, artifact, thread


def test_default_dry_run_does_not_delete_any_content(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    _, artifact, _ = run_artifact(db_session, auth_user, course)
    cache = AIResponseCache(key="expired", owner_id=auth_user.id, course_id=course.id, payload={"secret": 1}, model="x", expires_at=OLD)
    db_session.add(cache); db_session.commit()
    from scripts.purge_ai_history import purge
    plan = purge(db_session)
    assert plan["expired_cache_keys"] == ["expired"]
    assert plan["artifact_ids"] == []
    assert db_session.get(AgentArtifact, artifact.id).payload == {"private": "old source"}
    assert db_session.get(AIResponseCache, "expired")


def test_explicit_policy_requires_reviewed_matching_plan(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    _, artifact, _ = run_artifact(db_session, auth_user, course)
    with pytest.raises(ValueError, match="policy reference"):
        apply_retention(db_session, RetentionPolicy(artifact_days=30), expected_digest="x")
    policy = RetentionPolicy(artifact_days=30, policy_reference="approved-test-policy")
    with pytest.raises(ValueError, match="fresh dry-run"):
        apply_retention(db_session, policy, expected_digest="wrong", now=NOW)
    assert db_session.get(AgentArtifact, artifact.id)


def test_active_paused_retryable_and_published_provenance_are_preserved(db_session, auth_user):
    artifacts = []
    for status, retryable in (("running", False), ("paused", False), ("failed", True)):
        course = make_course(db_session, owner_id=auth_user.id)
        artifacts.append(run_artifact(db_session, auth_user, course, status=status, retryable=retryable)[1].id)
    published = make_course(db_session, owner_id=auth_user.id)
    artifacts.append(run_artifact(db_session, auth_user, published)[1].id)
    db_session.add(CourseVersion(course_id=published.id, revision=1, publication_status="published", name="Keep", created_at=OLD))
    db_session.commit()
    plan = retention_plan(db_session, RetentionPolicy(artifact_days=30), now=NOW)
    assert plan["artifact_ids"] == plan["checkpoint_thread_ids"] == []
    assert db_session.query(AgentArtifact).filter(AgentArtifact.id.in_(artifacts)).count() == 4


def test_terminal_unreferenced_artifact_and_owned_checkpoint_are_purged_once(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    run, artifact, thread = run_artifact(db_session, auth_user, course)
    artifact_id, thread_id = artifact.id, thread.thread_id
    policy = RetentionPolicy(artifact_days=30, policy_reference="approved-test")
    plan = retention_plan(db_session, policy, now=NOW)
    removed = []
    result = apply_retention(db_session, policy, expected_digest=plan["digest"], checkpoint_delete=removed.append, now=NOW)
    db_session.expire_all()
    assert removed == [thread_id]
    assert result["counts"]["artifact_ids"] == 1
    assert db_session.get(AgentArtifact, artifact_id) is None
    assert db_session.get(GenerationRun, run.id) is not None
    assert db_session.get(AIThreadReference, thread_id).purged_at == NOW
    second = retention_plan(db_session, policy, now=NOW)
    assert second["checkpoint_thread_ids"] == second["artifact_ids"] == []


def test_new_activity_invalidates_cleanup_plan(db_session, auth_user):
    course = make_course(db_session, owner_id=auth_user.id)
    run, artifact, _ = run_artifact(db_session, auth_user, course)
    policy = RetentionPolicy(artifact_days=30, policy_reference="approved-test")
    plan = retention_plan(db_session, policy, now=NOW)
    run.status = "running"
    db_session.commit()
    removed = []
    with pytest.raises(ValueError, match="changed"):
        apply_retention(db_session, policy, expected_digest=plan["digest"], checkpoint_delete=removed.append, now=NOW)
    assert removed == [] and db_session.get(AgentArtifact, artifact.id)


def test_history_retention_keeps_idempotency_and_pending_input(db_session, auth_user):
    chat = Chat(owner_id=auth_user.id, title="Retention")
    db_session.add(chat); db_session.flush()
    old = ChatMessage(chat_id=chat.id, role="user", content="private", created_at=OLD,
        message_metadata={"request_id": "stable-key", "request_hash": "digest", "request_status": "completed", "source_text": "remove"})
    pending = ChatMessage(chat_id=chat.id, role="user", content="still pending", created_at=OLD,
        message_metadata={"request_id": "pending-key", "request_status": "pending"})
    db_session.add_all([old, pending]); db_session.commit()
    policy = RetentionPolicy(history_days=90, policy_reference="approved-test")
    plan = retention_plan(db_session, policy, now=NOW)
    apply_retention(db_session, policy, expected_digest=plan["digest"], now=NOW)
    db_session.expire_all()
    assert old.content == "" and old.is_deleted
    assert old.message_metadata == {"request_id": "stable-key", "request_hash": "digest", "request_status": "completed"}
    assert pending.content == "still pending" and not pending.is_deleted


def test_draft_cleanup_preserves_latest_and_all_assessment_history(db_session, auth_user):
    org = Organization(name="Retention org")
    db_session.add(org); db_session.flush()
    course = make_course(db_session, owner_id=auth_user.id)
    versions = [CourseVersion(course_id=course.id, revision=i, publication_status="draft", name=f"Draft {i}", created_at=OLD) for i in (1, 2, 3)]
    db_session.add_all(versions); db_session.flush()
    module = ModuleVersion(course_version_id=versions[0].id, title="Old draft", created_at=OLD)
    db_session.add(module); db_session.flush()
    db_session.add(LessonVersion(module_version_id=module.id, title="Old draft lesson", content="old", created_at=OLD))
    assignment = LearningAssignment(organization_id=org.id, course_version_id=versions[1].id, user_id=auth_user.id,
        assigned_by=auth_user.id, rules_revision=1, rules={})
    db_session.add(assignment); db_session.flush()
    response = LearningResponse(assignment_id=assignment.id, assessment_id="task:1", attempt=1, answer={"text": "private answer"}, status="graded", points=1)
    db_session.add(response); db_session.flush()
    grade = LearningGradeEvent(response_id=response.id, actor_id=auth_user.id, points=1, feedback="Reviewed", source="manual")
    db_session.add(grade); db_session.commit()
    policy = RetentionPolicy(draft_version_days=90, policy_reference="approved-test")
    plan = retention_plan(db_session, policy, now=NOW)
    assert plan["draft_course_version_ids"] == [versions[0].id]
    ids = [version.id for version in versions]
    apply_retention(db_session, policy, expected_digest=plan["digest"], now=NOW)
    db_session.expire_all()
    assert db_session.get(CourseVersion, ids[0]) is None
    assert db_session.get(CourseVersion, ids[1]) and db_session.get(CourseVersion, ids[2])
    assert db_session.get(LearningResponse, response.id).answer == {"text": "private answer"}
    assert db_session.get(LearningGradeEvent, grade.id).points == 1


def test_official_sqlite_checkpoint_deletion_is_thread_scoped(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.base import empty_checkpoint
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        saver.setup()
        for key in ("old", "active"):
            saver.put({"configurable": {"thread_id": key, "checkpoint_ns": ""}}, empty_checkpoint(), {}, {})
        saver.delete_thread("old")
        assert saver.get_tuple({"configurable": {"thread_id": "old"}}) is None
        assert saver.get_tuple({"configurable": {"thread_id": "active"}}) is not None
