"""Explicit-policy housekeeping, never account-erasure or legal-hold processing.

No content deletion is enabled by default. Published content/provenance, source
documents, assignments, grades, usage and resumable work are always preserved.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
from sqlalchemy import select
from app.database.db import Base
from app.models.ai_state import AIResponseCache, ChatMemory
from app.models.ai_resources import AIResourceUsage, AIThreadReference
from app.models.agent_artifact import AgentArtifact
from app.models.chat import ChatMessage
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.generation_run import GenerationRun
from app.models.materials import LearnerAssistantMessage
from app.models.module_version import ModuleVersion
from app.models.lesson_version import LessonVersion
from app.models.test_version import TestVersion


@dataclass(frozen=True)
class RetentionPolicy:
    history_days: int | None = None
    artifact_days: int | None = None
    draft_version_days: int | None = None
    policy_reference: str = ""

    def __post_init__(self):
        for value in (self.history_days, self.artifact_days, self.draft_version_days):
            if value is not None and (type(value) is not int or not 1 <= value <= 36500):
                raise ValueError("Retention days must be an explicit positive integer")


def _ids(query, column):
    return sorted(row[0] for row in query.with_entities(column).all())


def _protected_versions(db):
    protected = set()
    # Discover real foreign keys, including integration tables. Only children
    # exclusively owned by a draft snapshot are deleted in dependency order.
    for table in Base.metadata.tables.values():
        if table.name in {"module_versions", "test_versions"}:
            continue
        for column in table.columns:
            if any(key.target_fullname == "course_versions.id" for key in column.foreign_keys):
                protected.update(value for (value,) in db.execute(select(column)).all() if value is not None)
    from app.models.learning import LearningProgram, LearningProgramEnrollment
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "course_version_id" and type(item) is int:
                    protected.add(item)
                else:
                    visit(item)
    for (value,) in db.query(LearningProgram.steps):
        visit(value)
    for (value,) in db.query(LearningProgramEnrollment.snapshot):
        visit(value)
    return protected


def retention_plan(db, policy: RetentionPolicy, *, now=None):
    now = now or datetime.utcnow()
    plan = {"policy": asdict(policy), "expired_cache_keys": _ids(db.query(AIResponseCache).filter(AIResponseCache.expires_at <= now), AIResponseCache.key),
        "chat_message_ids": [], "learner_message_ids": [], "chat_memory_ids": [], "artifact_ids": [],
        "checkpoint_thread_ids": [], "draft_course_version_ids": [], "lesson_version_ids": [], "module_version_ids": [], "test_version_ids": [],
        "preserved": ["published versions and their course provenance", "source documents and files", "active, paused or retryable jobs",
            "latest draft version", "assignments, submissions and grade history", "usage totals and idempotency receipts"]}
    if policy.history_days is not None:
        cutoff = now - timedelta(days=policy.history_days)
        old_chat = db.query(ChatMessage).filter(ChatMessage.created_at < cutoff, ChatMessage.is_deleted.is_(False))
        eligible = [row for row in old_chat if (row.message_metadata or {}).get("request_status") != "pending"]
        plan["chat_message_ids"] = sorted(row.id for row in eligible)
        chats = {row.chat_id for row in eligible}
        plan["chat_memory_ids"] = _ids(db.query(ChatMemory).filter(ChatMemory.chat_id.in_(chats)), ChatMemory.chat_id)
        plan["learner_message_ids"] = _ids(db.query(LearnerAssistantMessage).filter(LearnerAssistantMessage.created_at < cutoff,
            LearnerAssistantMessage.status.notin_({"deleted", "pending"})), LearnerAssistantMessage.id)
    published_courses = set(value for (value,) in db.query(CourseVersion.course_id).filter(
        (CourseVersion.publication_status == "published") | CourseVersion.published_at.is_not(None)))
    active_courses = set(value for (value,) in db.query(GenerationRun.course_id).filter(GenerationRun.status.in_({"queued", "running", "paused"})))
    active_courses.update(value for (value,) in db.query(AIResourceUsage.course_id).filter(AIResourceUsage.status.in_({"queued", "running"})))
    protected_courses = published_courses | active_courses
    if policy.artifact_days is not None:
        cutoff = now - timedelta(days=policy.artifact_days)
        from app.models.course_update_proposal import CourseUpdateProposal
        from app.models.course_source_link import CourseSourceLink
        protected_runs = set(value for (value,) in db.query(CourseUpdateProposal.detected_by_run_id) if value is not None)
        protected_runs.update(value for (value,) in db.query(CourseSourceLink.run_id) if value is not None)
        eligible_runs = []
        for run in db.query(GenerationRun).filter(GenerationRun.created_at < cutoff, GenerationRun.status.in_({"completed", "succeeded", "failed"})):
            course = db.get(Course, run.course_id)
            if (run.course_id in protected_courses or run.id in protected_runs or run.retryable or
                course and course.current_graph_id and (run.output or {}).get("graph_id") == course.current_graph_id):
                continue
            eligible_runs.append(run.id)
        plan["artifact_ids"] = _ids(db.query(AgentArtifact).filter(AgentArtifact.run_id.in_(eligible_runs), AgentArtifact.created_at < cutoff), AgentArtifact.id)
        plan["checkpoint_thread_ids"] = _ids(db.query(AIThreadReference).filter(AIThreadReference.run_id.in_(eligible_runs), AIThreadReference.purged_at.is_(None)), AIThreadReference.thread_id)
    if policy.draft_version_days is not None:
        cutoff = now - timedelta(days=policy.draft_version_days)
        protected = _protected_versions(db)
        versions = db.query(CourseVersion).order_by(CourseVersion.course_id, CourseVersion.revision.desc()).all()
        latest = {}
        for version in versions:
            latest.setdefault(version.course_id, version.id)
        plan["draft_course_version_ids"] = sorted(version.id for version in versions
            if version.publication_status == "draft" and version.published_at is None and version.created_at < cutoff
            and version.course_id not in protected_courses and version.id not in protected and version.id != latest[version.course_id])
        selected = plan["draft_course_version_ids"]
        plan["module_version_ids"] = _ids(db.query(ModuleVersion).filter(ModuleVersion.course_version_id.in_(selected)), ModuleVersion.id)
        plan["lesson_version_ids"] = _ids(db.query(LessonVersion).filter(LessonVersion.module_version_id.in_(plan["module_version_ids"])), LessonVersion.id)
        plan["test_version_ids"] = _ids(db.query(TestVersion).filter(
            TestVersion.course_version_id.in_(selected) | TestVersion.module_version_id.in_(plan["module_version_ids"])), TestVersion.id)
    plan["digest"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


def apply_retention(db, policy, *, expected_digest, checkpoint_delete=None, now=None):
    if not policy.policy_reference.strip():
        raise ValueError("An approved policy reference is required for execution")
    if not expected_digest:
        raise ValueError("Inspect a dry-run plan and supply its digest before execution")
    from app.services.ai_resources import locked
    db.execute(select(Course.id).with_for_update()).all()
    locked(db)
    plan = retention_plan(db, policy, now=now)
    if plan["digest"] != expected_digest:
        db.rollback()
        raise ValueError("Retention candidates changed; inspect a fresh dry-run plan")
    threads = plan["checkpoint_thread_ids"]
    if threads:
        if checkpoint_delete is not None:
            for thread in threads:
                checkpoint_delete(thread)
        else:
            from app.ai.checkpoints import checkpoint_store
            with checkpoint_store() as store:
                for thread in threads:
                    store.delete_thread(thread)
        # Idempotent store deletion can safely repeat after a later DB failure.
        db.query(AIThreadReference).filter(AIThreadReference.thread_id.in_(threads)).update({"purged_at": now or datetime.utcnow()}, synchronize_session=False)
    db.query(AIResponseCache).filter(AIResponseCache.key.in_(plan["expired_cache_keys"])).delete(synchronize_session=False)
    db.query(ChatMemory).filter(ChatMemory.chat_id.in_(plan["chat_memory_ids"])).delete(synchronize_session=False)
    for row in db.query(ChatMessage).filter(ChatMessage.id.in_(plan["chat_message_ids"])):
        row.content, row.is_deleted = "", True
        row.message_metadata = {key: value for key, value in (row.message_metadata or {}).items()
            if key in {"request_id", "request_hash", "request_digest", "request_status", "status", "error_code", "response_id", "question_id"}}
    db.query(LearnerAssistantMessage).filter(LearnerAssistantMessage.id.in_(plan["learner_message_ids"])).update(
        {"question": "", "answer": None, "citations": [], "status": "deleted"}, synchronize_session=False)
    for model, key in ((AgentArtifact, "artifact_ids"), (TestVersion, "test_version_ids"), (LessonVersion, "lesson_version_ids"),
                       (ModuleVersion, "module_version_ids"), (CourseVersion, "draft_course_version_ids")):
        db.query(model).filter(model.id.in_(plan[key])).delete(synchronize_session=False)
    db.commit()
    return {"executed_at": (now or datetime.utcnow()).isoformat(), "policy_reference": policy.policy_reference,
        "digest": plan["digest"], "counts": {key: len(value) for key, value in plan.items() if isinstance(value, list) and key != "preserved"}}
