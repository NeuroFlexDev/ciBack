from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.version import runtime_signature
from app.core.config import settings
from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.course_source_link import CourseSourceLink
from app.models.document import Document
from app.models.domain_enums import (
    CourseGraphStatus,
    CourseStatus,
    DocumentStatus,
    GenerationRunStatus,
    GenerationRunType,
)
from app.models.generation_run import GenerationRun
from app.repositories.pipeline import PipelineRepository
from app.schemas.pipeline import GeneratedGraphPayload
from app.services.agent_runtime import AgentRuntime
from app.services.agentic_course_pipeline import AgenticCoursePipeline
from app.services.ai_resources import ResourceOutcome, ResourceReservation
from app.services.course_generation_settings_service import (
    generation_settings_snapshot,
    settings_not_found,
)
from app.services.course_materialization_service import CourseMaterializationService
from app.services.course_update_service import CourseUpdateService
from app.services.document_processing import chunk_blocks
from app.services.document_safety import bounded_extract_blocks as extract_blocks
from app.services.embedding_service import replace_document_embeddings
from app.services.file_storage import FileStorage
from app.services.generation_service import DEFAULT_ENGINE, generate_from_prompt
from app.services.source_catalog_service import build_source_catalog, graph_source_links

logger = logging.getLogger(__name__)


class PipelineRunFailed(Exception):
    def __init__(self, run_id: int, message: str, status_code: int = 500):
        super().__init__(message)
        self.run_id = run_id
        self.message = message
        self.status_code = status_code


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _safe_error(exc: Exception) -> str:
    from app.ai.gateway import safe_error

    return safe_error(exc)


def _safe_document_error(exc: Exception) -> str:
    if isinstance(exc, (ValueError, UnicodeError)):
        return "Не удалось извлечь текст из документа"
    return "Не удалось обработать документ"


def _apply_assessment_settings(
    nodes: list[dict], edges: list[dict], snapshot: dict
) -> tuple[list[dict], list[dict]]:
    # Legacy imports must contain real questions. Never manufacture empty tests
    # merely to make a diagram match a requested test-count setting.
    removed = {
        node["id"]
        for node in nodes
        if node.get("type") == "test"
        and not snapshot[
            "final_test_enabled"
            if node.get("assessment_scope") == "final"
            else "module_tests_enabled"
        ]
    }
    nodes = [node for node in nodes if node["id"] not in removed]
    edges = [
        edge for edge in edges if edge["source"] not in removed and edge["target"] not in removed
    ]
    tests = {node["id"]: node for node in nodes if node.get("type") == "test"}
    if snapshot["module_tests_enabled"]:
        for module in (node for node in nodes if node.get("type") == "module"):
            if not any(
                edge["source"] == module["id"]
                and edge["target"] in tests
                and tests[edge["target"]].get("assessment_scope") == "module"
                for edge in edges
            ):
                raise ValueError("generated module has no real assessment")
    if snapshot["final_test_enabled"] and not any(
        node.get("assessment_scope") == "final" for node in tests.values()
    ):
        raise ValueError("generated course has no real final assessment")
    return nodes, edges


@dataclass(frozen=True, kw_only=True)
class GraphGenerationRequest:
    """Parameters for prepare_graph_generation, preserving its original defaults."""

    document_ids: list[int]
    request_id: str | None = None
    web_search_enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class ReindexOptions:
    """Parameters for reindex_document, preserving its original defaults."""

    run_id: int | None = None
    queue_only: bool = False


class PipelineService:
    GENERATION_STAGES = (
        ("ingestion", "Извлечение структуры документов"),
        ("competency_mapping", "Карта компетенций"),
        ("course_architecture", "Архитектура курса"),
        ("lesson_writing", "Написание уроков"),
        ("assessment_generation", "Оценочные материалы"),
        ("quality_assurance", "Проверка качества"),
        ("materialization", "Сохранение курса"),
    )

    @staticmethod
    def _checkpoint(db: Session, run: GenerationRun, stage: str, progress: int) -> None:
        from app.services.generation_control import check_cancelled

        check_cancelled(db, run.id)
        run.current_stage = stage
        run.progress_percent = progress
        db.commit()

    @staticmethod
    def validate_generation_documents(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        document_ids: list[int],
        request_id: str | None = None,
    ) -> None:
        course = PipelineRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        selected_ids = list(dict.fromkeys(document_ids))
        documents = PipelineRepository.selected_indexed_documents(
            db, course_id, owner_id, selected_ids
        )
        if len(documents) != len(selected_ids):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "documents_not_ready",
                    "message": "Документы не найдены, не принадлежат курсу или ещё не проиндексированы",
                },
            )

    @staticmethod
    def prepare_graph_generation(
        db: Session, *, course_id: int, owner_id: int, request: GraphGenerationRequest
    ) -> GenerationRun:
        document_ids = request.document_ids
        request_id = request.request_id
        web_search_enabled = request.web_search_enabled
        course = PipelineRepository.get_owned_course(db, course_id, owner_id, for_update=True)
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        generation_settings = PipelineRepository.get_generation_settings(db, course_id)
        if generation_settings is None:
            raise settings_not_found()
        active = PipelineRepository.active_graph_run(db, course_id, owner_id)
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "generation_run_active",
                    "message": "Генерация курса уже запущена",
                    "run_id": active.id,
                },
            )
        selected_ids = list(dict.fromkeys(document_ids))
        documents = PipelineRepository.selected_indexed_documents(
            db, course_id, owner_id, selected_ids
        )
        if len(documents) != len(selected_ids):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "documents_not_ready",
                    "message": "Документы не найдены, не принадлежат курсу или ещё не проиндексированы",
                },
            )
        settings_snapshot = generation_settings_snapshot(course, generation_settings)
        settings_snapshot["web_search_enabled"] = web_search_enabled
        from app.services.organization_ai_context import organization_ai_context

        settings_snapshot["organization_context"] = organization_ai_context(
            db, course.organization_id
        )
        from app.services.ai_entitlements import unlimited_ai
        from app.services.generation_preflight import require_preflight

        require_preflight(
            documents, settings_snapshot, unlimited=unlimited_ai(db, course.organization_id)
        )
        docs_snapshot = [
            {"document_id": item.id, "version": item.version, "content_hash": item.content_hash}
            for item in documents
        ]
        fingerprint = hashlib.sha256(
            (
                json.dumps(docs_snapshot, sort_keys=True)
                + json.dumps(settings_snapshot, sort_keys=True)
                + "agentic-pipeline-v2:"
                + runtime_signature()
            ).encode()
        ).hexdigest()
        from app.services.organization_access import check_generation_limit

        check_generation_limit(db, owner_id)
        now = datetime.utcnow()
        run = GenerationRun(
            owner_id=owner_id,
            request_id=request_id,
            course_id=course_id,
            run_type=GenerationRunType.GRAPH_GENERATION.value,
            status=GenerationRunStatus.QUEUED.value,
            current_stage="queued",
            progress_percent=0,
            queued_at=now,
            prompt="agentic-pipeline-v2:" + runtime_signature(),
            model=DEFAULT_ENGINE,
            input_docs=docs_snapshot,
            input_documents_snapshot=docs_snapshot,
            settings_snapshot=settings_snapshot,
            input_fingerprint=fingerprint,
        )
        db.add(run)
        course.status = CourseStatus.GENERATING.value
        try:
            db.flush()
            from app.services.ai_resources import reserve

            reserve(
                db,
                reservation=ResourceReservation(
                    owner_id=owner_id,
                    course_id=course_id,
                    operation="generation",
                    lane="background",
                    key=f"generation:{run.id}",
                    run_id=run.id,
                    queued=True,
                ),
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            active = PipelineRepository.active_graph_run(db, course_id, owner_id)
            if active is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "generation_run_active",
                        "message": "Генерация курса уже запущена",
                        "run_id": active.id,
                    },
                ) from exc
            raise
        db.refresh(run)
        return run

    @staticmethod
    def fail_run(db: Session, run_id: int, *, code: str = "queue_unavailable") -> None:
        run = db.get(GenerationRun, run_id)
        if run is not None and run.status in {
            GenerationRunStatus.QUEUED.value,
            GenerationRunStatus.RUNNING.value,
        }:
            run.status = GenerationRunStatus.FAILED.value
            run.error_code = code
            run.error_message = (
                "Не удалось поставить генерацию в очередь"
                if code == "queue_unavailable"
                else "Не удалось завершить генерацию курса. Попробуйте ещё раз."
            )
            run.retryable = True
            run.finished_at = datetime.utcnow()
            course = db.get(Course, run.course_id)
            if course is not None:
                course.status = CourseStatus.GENERATION_FAILED.value
            db.commit()

    fail_enqueue = fail_run

    @staticmethod
    def pause_enqueue(db: Session, run_id: int) -> None:
        """Keep the accepted job resumable when its queue is unavailable."""
        run = db.get(GenerationRun, run_id)
        # An enqueue acknowledgement may be lost after a worker starts. Do not
        # overwrite running work in that case.
        if run is not None:
            db.refresh(run)
        if run is not None and run.status == GenerationRunStatus.QUEUED.value:
            run.status = GenerationRunStatus.PAUSED.value
            run.error_code = "queue_unavailable"
            run.error = run.error_message = None
            run.retryable = True
            course = db.get(Course, run.course_id)
            if course is not None:
                course.status = (
                    CourseStatus.READY.value
                    if course.current_graph
                    else CourseStatus.CONFIGURED.value
                )
            db.commit()

    @staticmethod
    def get_document(db: Session, document_id: int, owner_id: int):
        document = PipelineRepository.get_owned_document(db, document_id, owner_id)
        if document is None or document.status == DocumentStatus.ARCHIVED.value:
            raise HTTPException(status_code=404, detail="Документ не найден")
        return document

    @staticmethod
    def get_run(db: Session, run_id: int, owner_id: int) -> GenerationRun:
        run = PipelineRepository.get_owned_run(db, run_id, owner_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Запуск не найден")
        return run

    @staticmethod
    def get_run_status(db: Session, run_id: int, owner_id: int) -> dict:
        run = PipelineService.get_run(db, run_id, owner_id)
        from app.services.ai_resources import reconcile_expired_run

        reconcile_expired_run(db, run)
        data = {column.name: getattr(run, column.name) for column in run.__table__.columns}
        data["run_id"] = run.id
        from app.services.web_search_service import status as web_search_status

        data["web_search"] = web_search_status(db, run.id)
        from app.ai.offline import GENERATION_PAUSED, GENERATION_QUEUED

        system_pause_notices = {
            "worker_result_unknown": "Рабочий процесс не подтвердил завершение. Автоматический платный повтор остановлен; готовые этапы сохранены. Вы можете продолжить генерацию.",
            "time_limit": "Достигнут лимит времени генерации. Готовые этапы сохранены; вы можете продолжить генерацию.",
        }
        system_pause = run.status == "paused" and run.error_code in system_pause_notices
        data["status_notice"] = (
            system_pause_notices.get(run.error_code, GENERATION_PAUSED)
            if run.status == "paused"
            else GENERATION_QUEUED
            if run.status == "queued" and run.error_code == "ai_unavailable"
            else None
        )
        if data["status_notice"]:
            data["error"] = None
        if run.cancel_requested and not system_pause:
            data["status_notice"] = (
                "Генерация остановлена. Завершённые этапы сохранены; можно продолжить."
                if run.status == "paused"
                else "Останавливаем генерацию после текущего запроса AI. Завершённые этапы сохраняются."
            )
        data["stages"] = []
        if run.run_type == GenerationRunType.GRAPH_GENERATION.value:
            codes = [item[0] for item in PipelineService.GENERATION_STAGES]
            current_index = codes.index(run.current_stage) if run.current_stage in codes else -1
            terminal_success = run.status in {
                GenerationRunStatus.COMPLETED.value,
                GenerationRunStatus.SUCCEEDED.value,
            }
            for index, (code, title) in enumerate(PipelineService.GENERATION_STAGES):
                if terminal_success or index < current_index:
                    stage_status = "completed"
                elif index == current_index and run.status in {
                    GenerationRunStatus.RUNNING.value,
                    GenerationRunStatus.FAILED.value,
                }:
                    stage_status = "running"
                else:
                    stage_status = "pending"
                data["stages"].append({"code": code, "title": title, "status": stage_status})
        data["status_error"] = (
            {
                "code": run.error_code or "generation_failed",
                "message": run.error_message
                or "Не удалось завершить генерацию курса. Попробуйте ещё раз.",
                "retryable": run.retryable,
            }
            if run.status == GenerationRunStatus.FAILED.value
            else None
        )
        if data["status_error"] is not None:
            data["error"] = data["status_error"]["message"]
        return data

    @staticmethod
    def get_run_artifacts(db: Session, run_id: int, owner_id: int):
        artifacts = PipelineRepository.list_agent_artifacts(db, run_id=run_id, owner_id=owner_id)
        if artifacts is None:
            raise HTTPException(status_code=404, detail="Запуск не найден")
        return artifacts

    @staticmethod
    def retry_graph_generation(db: Session, run_id: int, owner_id: int) -> GenerationRun:
        original = PipelineService.get_run(db, run_id, owner_id)
        # Serialize retries for the course, then refresh status after acquiring
        # the lock: another request may already have resumed this same run.
        course = PipelineRepository.get_owned_course(
            db, original.course_id, owner_id, for_update=True
        )
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        db.refresh(original)
        if (
            original.run_type != GenerationRunType.GRAPH_GENERATION.value
            or original.status
            not in {GenerationRunStatus.FAILED.value, GenerationRunStatus.PAUSED.value}
            or not original.retryable
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "generation_run_not_retryable",
                    "message": "Запуск нельзя повторить",
                },
            )
        active = PipelineRepository.active_graph_run(db, original.course_id, owner_id)
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "generation_run_active",
                    "message": "Генерация курса уже запущена",
                    "run_id": active.id,
                },
            )
        documents = PipelineRepository.documents_for_snapshot(
            db, original.course_id, owner_id, original.input_documents_snapshot
        )
        actual = {(item.id, item.version, item.content_hash) for item in documents}
        expected = {
            (item["document_id"], item["version"], item["content_hash"])
            for item in original.input_documents_snapshot
        }
        if actual != expected:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "retry_input_unavailable",
                    "message": "Исходные версии документов недоступны",
                },
            )
        # Resume the same durable checkpoint and cost ledger for every retryable
        # failure. A worker restart is not a new course or a new monthly launch.
        from app.services.ai_resources import prepare_generation_retry

        prepare_generation_retry(db, original)
        original.status = GenerationRunStatus.QUEUED.value
        original.cancel_requested = False
        original.queued_at = datetime.utcnow()
        original.started_at = None
        original.finished_at = None
        original.error = original.error_code = original.error_message = None
        original.attempt += 1
        course.status = CourseStatus.GENERATING.value
        db.commit()
        return original

    @staticmethod
    def reindex_document(
        db: Session,
        *,
        document_id: int,
        owner_id: int,
        storage: FileStorage,
        options: ReindexOptions | None = None,
    ) -> GenerationRun:
        return _DocumentIndexing(db, document_id, owner_id, storage, options).execute()

    @staticmethod
    def generate_graph(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        force: bool,
        prepared_run_id: int | None = None,
    ) -> GenerationRun:
        return _GraphGeneration(db, course_id, owner_id, force, prepared_run_id).execute()


class _DocumentIndexing:
    def __init__(self, db, document_id, owner_id, storage, options):
        self.db = db
        self.document_id = document_id
        self.owner_id = owner_id
        self.storage = storage
        self.options = options

    def _activate_document_version(self):
        if self.document.supersedes_document_id is not None:
            previous = self.db.get(Document, self.document.supersedes_document_id)
            if previous is None or previous.document_key != self.document.document_key:
                raise ValueError("Некорректная цепочка версий документа")
            if previous.learner_visible:
                course = PipelineRepository.get_owned_course(
                    self.db, self.document.course_id, self.owner_id, for_update=True
                )
                course.content_revision += 1
            previous.is_current = False
            self.db.flush()
            self.document.is_current = True

    def _index_chunks(self, chunks):
        chunk_models = PipelineRepository.replace_chunks(self.db, self.document, chunks)
        vector_chunks = [
            {
                "chunk_id": chunk_model.id,
                "chunk_index": chunk_model.chunk_index,
                "text": chunk_model.text,
                "page": chunk_model.page,
                "section": chunk_model.section,
                "source": self.document.original_filename,
                "source_type": self.document.source_type,
                "owner_id": self.document.owner_id,
                "organization_id": self.document.course.organization_id,
                "course_id": self.document.course_id,
            }
            for chunk_model in chunk_models
        ]
        from app.ai import gateway

        if gateway.configured() or settings.ENV != "test":
            from app.ai.retrieval import persist_embeddings

            embedding_ids = persist_embeddings(self.db, chunk_models)
        else:
            embedding_ids = replace_document_embeddings(
                self.document.id, self.document.version, vector_chunks
            )
        for chunk_model, embedding_id in zip(chunk_models, embedding_ids, strict=True):
            chunk_model.embedding_id = embedding_id
        return chunk_models, embedding_ids

    def _analyze_replacement(self):
        if self.document.supersedes_document_id is not None:
            try:
                proposal = CourseUpdateService.analyze_replacement(
                    self.db,
                    document=self.document,
                    run=self.run,
                    generate=generate_from_prompt,
                )
                if proposal is not None:
                    self.run.output = {
                        **(self.run.output or {}),
                        "update_proposal_id": proposal.id,
                    }
                    self.db.commit()
            except Exception as update_exc:
                self.db.rollback()
                logger.warning(
                    "Update impact analysis failed run_id=%s error=%s",
                    self.run.id,
                    update_exc.__class__.__name__,
                )

    def _record_index_failure(self, exc):
        from app.services.ai_resources import finish

        self.db.rollback()
        error = _safe_error(exc)
        failed_run = self.db.get(GenerationRun, self.run_id)
        failed_document = PipelineRepository.get_owned_document(
            self.db, self.document_id, self.owner_id
        )
        if failed_run is not None:
            failed_run.status = GenerationRunStatus.FAILED.value
            failed_run.error = error
            failed_run.latency_ms = _elapsed_ms(self.started)
        if failed_document is not None and failed_document.status != DocumentStatus.INDEXED.value:
            failed_document.status = DocumentStatus.FAILED.value
            failed_document.processing_error = _safe_document_error(exc)
        self.db.commit()
        finish(self.db, self.resource, outcome=ResourceOutcome(failed=True))
        return error

    def _prepare_index_run(self):
        if self.run_id is not None:
            self.run = self.db.get(GenerationRun, self.run_id)
            if (
                self.run is None
                or self.run.run_type != GenerationRunType.DOCUMENT_INDEX.value
                or self.run.document_id != self.document.id
                or self.run.owner_id != self.owner_id
                or self.run.status
                not in {GenerationRunStatus.QUEUED.value, GenerationRunStatus.RUNNING.value}
            ):
                return False
            from app.models.ai_resources import AIResourceUsage

            self.resource = (
                self.db.query(AIResourceUsage).filter_by(key=f"document:{self.run.id}").first()
            )
            if self.resource is None:
                from app.services.ai_resources import reserve

                self.resource = reserve(
                    self.db,
                    reservation=ResourceReservation(
                        owner_id=self.owner_id,
                        course_id=self.document.course_id,
                        operation="document",
                        lane="background",
                        key=f"document:{self.run.id}",
                        run_id=self.run.id,
                        timeout=settings.AI_JOB_TIMEOUT_SECONDS,
                        partial_ocr=True,
                        pages=settings.AI_OCR_MAX_PAGES
                        if self.document.mime_type == "application/pdf" and settings.AI_OCR_ENABLED
                        else 0,
                    ),
                )
            self.run.status = GenerationRunStatus.RUNNING.value
            if self.document.status != DocumentStatus.INDEXED.value:
                self.document.status = DocumentStatus.PROCESSING.value
            self.db.commit()
        else:
            active_run = (
                self.db.query(GenerationRun)
                .filter(
                    GenerationRun.document_id == self.document.id,
                    GenerationRun.run_type == GenerationRunType.DOCUMENT_INDEX.value,
                    GenerationRun.status.in_(
                        (GenerationRunStatus.QUEUED.value, GenerationRunStatus.RUNNING.value)
                    ),
                    GenerationRun.is_deleted.is_(False),
                )
                .order_by(GenerationRun.id.desc())
                .first()
            )
            if active_run is not None:
                self.run = active_run
                return False
            fingerprint = hashlib.sha256(
                f"{self.document.id}:{self.document.version}:{self.document.content_hash}".encode()
            ).hexdigest()
            input_docs = [
                {
                    "document_id": self.document.id,
                    "version": self.document.version,
                    "content_hash": self.document.content_hash,
                }
            ]
            self.run = GenerationRun(
                owner_id=self.owner_id,
                course_id=self.document.course_id,
                document_id=self.document.id,
                run_type=GenerationRunType.DOCUMENT_INDEX.value,
                status=GenerationRunStatus.QUEUED.value,
                input_docs=input_docs,
                input_fingerprint=fingerprint,
            )
            if self.document.status != DocumentStatus.INDEXED.value:
                self.document.status = DocumentStatus.QUEUED.value
            self.document.processing_error = None
            self.db.add(self.run)
            self.db.flush()
            from app.services.ai_resources import reserve

            self.resource = reserve(
                self.db,
                reservation=ResourceReservation(
                    owner_id=self.owner_id,
                    course_id=self.document.course_id,
                    operation="document",
                    lane="background",
                    key=f"document:{self.run.id}",
                    run_id=self.run.id,
                    queued=self.queue_only,
                    timeout=settings.AI_JOB_TIMEOUT_SECONDS,
                    partial_ocr=True,
                    pages=settings.AI_OCR_MAX_PAGES
                    if self.document.mime_type == "application/pdf" and settings.AI_OCR_ENABLED
                    else 0,
                ),
            )
            self.db.commit()
            self.db.refresh(self.run)
            self.run_id = self.run.id
            if self.queue_only:
                from app.services.job_queue import enqueue_document_index

                enqueue_document_index(self.run.id)
                return False
        return True

    def execute(self):
        self.options = self.options or ReindexOptions()
        self.run_id = self.options.run_id
        self.queue_only = self.options.queue_only
        self.document = PipelineRepository.get_owned_document(
            self.db, self.document_id, self.owner_id
        )
        if self.document is None:
            raise HTTPException(status_code=404, detail="Документ не найден")
        if self.document.source_type == "web_search":
            raise HTTPException(
                409,
                {
                    "code": "web_snapshot_immutable",
                    "message": "Снимок интернет-источника неизменяем. Новый поиск создаёт новый снимок.",
                },
            )
        if self.document.status == DocumentStatus.ARCHIVED.value:
            raise HTTPException(status_code=409, detail="Документ архивирован")

        proceed = self._prepare_index_run()
        if not proceed:
            return self.run
        self.started = time.perf_counter()
        try:
            from app.services.ai_resources import finish

            content = self.storage.read_bytes(self.document.storage_key)
            blocks, extraction_usage = extract_blocks(
                content,
                self.document.mime_type,
                with_usage=True,
                ocr_page_limit=self.resource.ocr_pages,
            )
            self.resource.ocr_pages = extraction_usage.get("ocr_pages", self.resource.ocr_pages)
            self.db.commit()
            if sum(len(block.text) for block in blocks) > settings.DOCUMENT_MAX_EXTRACTED_CHARS:
                raise ValueError("Извлечённый текст превышает лимит; разделите документ")
            chunks = chunk_blocks(
                blocks,
                max_chars=settings.DOCUMENT_CHUNK_CHARS,
                overlap_chars=settings.DOCUMENT_CHUNK_OVERLAP_CHARS,
            )
            if not chunks:
                raise ValueError("В документе не найден текст для индексации")

            chunk_models, embedding_ids = self._index_chunks(chunks)
            self._activate_document_version()
            self.document.status = DocumentStatus.INDEXED.value
            self.document.processing_error = None
            self.run.status = GenerationRunStatus.SUCCEEDED.value
            self.run.latency_ms = _elapsed_ms(self.started)
            self.run.output = {
                "document_id": self.document.id,
                "document_version": self.document.version,
                "chunk_count": len(chunk_models),
                "embedding_count": len(embedding_ids),
                "semantic_embedding_count": sum(value.startswith("db:") for value in embedding_ids),
                "ocr_pages": self.resource.ocr_pages,
            }
            self.db.commit()
            finish(self.db, self.resource)
            self._analyze_replacement()
            self.db.refresh(self.run)
            return self.run
        except Exception as exc:
            error = self._record_index_failure(exc)
            status_code = 422 if isinstance(exc, (ValueError, UnicodeError)) else 503
            raise PipelineRunFailed(self.run_id, error, status_code) from exc


class _GraphGeneration:
    def __init__(self, db, course_id, owner_id, force, prepared_run_id):
        self.db = db
        self.course_id = course_id
        self.owner_id = owner_id
        self.force = force
        self.prepared_run_id = prepared_run_id

    def _prepare_graph_inputs(self):
        self.generation_settings = PipelineRepository.get_generation_settings(
            self.db, self.course_id
        )
        if self.generation_settings is None:
            raise settings_not_found()
        if self.course.status == CourseStatus.GENERATING.value and self.prepared_run is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "course_generation_in_progress",
                    "message": "Генерация курса уже выполняется",
                },
            )
        if self.prepared_run is None and self.course.status not in {
            CourseStatus.CONFIGURED.value,
            CourseStatus.GENERATION_FAILED.value,
            CourseStatus.READY.value,
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "course_not_configured",
                    "message": "Курс не готов к генерации",
                },
            )
        self.settings_snapshot = (
            self.prepared_run.settings_snapshot
            if self.prepared_run is not None
            else generation_settings_snapshot(self.course, self.generation_settings)
        )
        if self.prepared_run is None:
            from app.services.organization_ai_context import organization_ai_context

            self.settings_snapshot["organization_context"] = organization_ai_context(
                self.db, self.course.organization_id
            )
        if self.prepared_run is not None:
            self.documents = PipelineRepository.documents_for_snapshot(
                self.db,
                self.course_id,
                self.owner_id,
                self.prepared_run.input_documents_snapshot,
            )
        else:
            self.documents = PipelineRepository.indexed_documents(
                self.db, self.course_id, self.owner_id
            )
        if not self.documents:
            raise HTTPException(status_code=409, detail="У курса нет проиндексированных документов")

        self.input_docs = [
            {
                "document_id": document.id,
                "version": document.version,
                "content_hash": document.content_hash,
            }
            for document in self.documents
        ]
        documents_fingerprint = "|".join(
            f"{item['document_id']}:{item['version']}:{item['content_hash']}"
            for item in self.input_docs
        )
        fingerprint_source = (
            documents_fingerprint
            + "|"
            + json.dumps(self.settings_snapshot, ensure_ascii=False, sort_keys=True)
            + "|agentic-pipeline-v2:"
            + runtime_signature()
        )
        self.fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()

    def _build_graph(self):
        self.run.status = GenerationRunStatus.RUNNING.value
        self.run.started_at = datetime.utcnow()
        source_chars = sum(
            len(str(chunk.text or "").strip())
            for document in self.documents
            for chunk in document.chunks
        )
        if source_chars > settings.AI_MAX_SOURCE_CHARS:
            raise ValueError("Source corpus exceeds AI_MAX_SOURCE_CHARS; split it into courses")
        self.source_catalog = build_source_catalog(
            self.documents, max_chars=settings.AI_MAX_SOURCE_CHARS
        )
        if not self.source_catalog:
            raise ValueError("В документах нет доступных фрагментов для генерации")
        runtime = AgentRuntime(
            db=self.db,
            run_id=self.run.id,
            course_id=self.course_id,
            generate=generate_from_prompt,
        )
        pipeline = AgenticCoursePipeline(
            runtime=runtime,
            checkpoint=lambda stage, progress: PipelineService._checkpoint(
                self.db, self.run, stage, progress
            ),
        )
        self.build = pipeline.run(
            course_title=self.course.name,
            settings_snapshot=self.settings_snapshot,
            source_catalog=self.source_catalog,
        )
        payload = GeneratedGraphPayload.model_validate(
            {"nodes": self.build.nodes, "edges": self.build.edges}
        )
        self.nodes, self.edges = payload.json_payload()
        generated_lessons = sum(node.get("type") == "lesson" for node in self.nodes)
        if generated_lessons != self.settings_snapshot["lesson_count"]:
            raise ValueError("Generated lesson count does not match generation settings")
        if self.build.legacy_fallback:
            self.nodes, self.edges = _apply_assessment_settings(
                self.nodes, self.edges, self.settings_snapshot
            )

    def _materialize_graph(self):
        self.locked_course = PipelineRepository.get_owned_course(
            self.db, self.course_id, self.owner_id, for_update=True
        )
        from app.services.generation_control import check_cancelled

        check_cancelled(self.db, self.run.id)
        if self.locked_course is None:
            raise ValueError("Курс был удалён во время генерации")
        self.graph = CourseGraph(
            course_id=self.course_id,
            version=PipelineRepository.next_graph_version(self.db, self.course_id),
            nodes=[],
            edges=[],
            created_by=self.owner_id,
            status=CourseGraphStatus.DRAFT.value,
        )
        if (
            self.locked_course.current_graph is not None
            and self.locked_course.current_graph.status == CourseGraphStatus.DRAFT.value
        ):
            self.locked_course.current_graph.status = CourseGraphStatus.ARCHIVED.value
        self.db.add(self.graph)
        self.db.flush()
        self.locked_course.current_graph = self.graph
        self.materialized = CourseMaterializationService.materialize(
            self.db, course=self.locked_course, nodes=self.nodes, edges=self.edges
        )
        self.persisted_nodes = self.materialized.pop("canvas_nodes")
        self.persisted_edges = self.materialized.pop("canvas_edges")
        self.graph.nodes = self.persisted_nodes
        self.graph.edges = self.persisted_edges
        self.learning_map = (
            CourseMaterializationService.materialize_learning_map(
                self.db, course=self.locked_course, result=self.build.result
            )
            if self.build.result is not None
            else {"competency_count": 0, "learning_objective_count": 0}
        )
        self.link_payloads = graph_source_links(self.persisted_nodes, self.source_catalog)
        PipelineRepository.add_source_links(
            self.db,
            [
                CourseSourceLink(
                    course_id=self.course_id,
                    graph_id=self.graph.id,
                    run_id=self.run.id,
                    **item,
                )
                for item in self.link_payloads
            ],
        )
        self.locked_course.status = CourseStatus.READY.value

    def _describe_graph_failure(self, failed_run, exc):
        if getattr(exc, "ai_failure_kind", None) == "invalid_json":
            failed_run.error_code = "ai_invalid_response"
            failed_run.error_message = "Ответ AI не соответствует формату учебного материала. Завершённые этапы сохранены; можно повторить оставшийся этап."
        if isinstance(exc, HTTPException) and exc.status_code in {402, 413, 422}:
            failed_run.error_code = {
                402: "ai_budget_exhausted",
                413: "ai_context_exceeded",
                422: "ai_request_rejected",
            }[exc.status_code]
            failed_run.error_message = {
                402: "Исчерпан лимит AI-запуска. Уменьшите курс или измените бюджет в настройках сервера.",
                413: "Не удалось безопасно разместить один учебный блок в доступном контексте AI. Источники и завершённые этапы сохранены; скорректируйте блок или согласованные ограничения модели.",
                422: "AI-сервис отклонил запрос. Измените материалы или параметры генерации.",
            }[exc.status_code]
            failed_run.retryable = False
            if exc.status_code == 413 and getattr(exc, "ai_failure_kind", None) == "context_length":
                failed_run.error_message = (
                    "AI-провайдер отклонил размер запроса. Источники и завершённые этапы сохранены; автоматический повтор остановлен."
                    if getattr(exc, "request_sent", False)
                    else "Один учебный блок и резерв ответа превышают установленный лимит контекста. Источники и завершённые этапы сохранены; проверьте размер блока или согласованные ограничения модели."
                )

    def _record_graph_failure(self, exc):
        self.db.rollback()
        error = _safe_error(exc)
        failed_run = self.db.get(GenerationRun, self.run_id)
        failed_course = PipelineRepository.get_owned_course(
            self.db, self.course_id, self.owner_id, for_update=True
        )
        if failed_run is not None:
            failed_run.status = GenerationRunStatus.FAILED.value
            failed_run.error = error
            failed_run.error_code = "generation_failed"
            failed_run.error_message = "Не удалось сгенерировать курс"
            failed_run.retryable = True
            self._describe_graph_failure(failed_run, exc)
            failed_run.finished_at = datetime.utcnow()
            failed_run.latency_ms = _elapsed_ms(self.started)
        if failed_course is not None:
            failed_course.status = CourseStatus.GENERATION_FAILED.value
        from app.ai.providers import AIUnavailable
        from app.services.generation_control import GenerationCancelled

        if isinstance(exc, GenerationCancelled) and failed_run is not None:
            failed_run.status = GenerationRunStatus.PAUSED.value
            failed_run.error_code = exc.code
            failed_run.error_message = str(exc)
            failed_run.retryable = True
            if failed_course is not None:
                failed_course.status = (
                    CourseStatus.READY.value
                    if failed_course.current_graph
                    else CourseStatus.CONFIGURED.value
                )
        if isinstance(exc, AIUnavailable) and failed_run is not None:
            from app.ai.offline import GENERATION_PAUSED

            failed_run.status = GenerationRunStatus.PAUSED.value
            failed_run.error_code = "ai_unavailable"
            failed_run.error_message = GENERATION_PAUSED
            if failed_course is not None:
                failed_course.status = (
                    CourseStatus.READY.value
                    if failed_course.current_graph
                    else CourseStatus.CONFIGURED.value
                )
        if failed_run is not None:
            from app.services.notifications import generation_finished

            generation_finished(self.db, failed_run, failed_course)
        self.db.commit()
        return error

    def execute(self):
        self.course = PipelineRepository.get_owned_course(self.db, self.course_id, self.owner_id)
        if self.course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        self.prepared_run = (
            self.db.get(GenerationRun, self.prepared_run_id)
            if self.prepared_run_id is not None
            else None
        )
        self._prepare_graph_inputs()
        if not self.force:
            existing = PipelineRepository.latest_successful_graph_run(
                self.db, self.owner_id, self.course_id, self.fingerprint
            )
            graph_id = (existing.output or {}).get("graph_id") if existing else None
            if (
                existing is not None
                and isinstance(graph_id, int)
                and PipelineRepository.graph_by_id(self.db, graph_id) is not None
            ):
                self.course.status = CourseStatus.READY.value
                self.db.commit()
                return existing

        if self.prepared_run is None:
            from app.services.ai_entitlements import unlimited_ai
            from app.services.generation_preflight import require_preflight

            require_preflight(
                self.documents,
                self.settings_snapshot,
                unlimited=unlimited_ai(self.db, self.course.organization_id),
            )
            from app.services.organization_access import check_generation_limit

            check_generation_limit(self.db, self.owner_id)
        self.run = self.prepared_run or GenerationRun(
            owner_id=self.owner_id,
            course_id=self.course_id,
            run_type=GenerationRunType.GRAPH_GENERATION.value,
            status=GenerationRunStatus.QUEUED.value,
            prompt="agentic-pipeline-v2:" + runtime_signature(),
            model=DEFAULT_ENGINE,
            input_docs=self.input_docs,
            settings_snapshot=self.settings_snapshot,
            input_fingerprint=self.fingerprint,
        )
        if self.prepared_run is None:
            self.db.add(self.run)
            self.db.flush()
            from app.services.ai_resources import reserve

            reserve(
                self.db,
                reservation=ResourceReservation(
                    owner_id=self.owner_id,
                    course_id=self.course_id,
                    operation="generation",
                    lane="background",
                    key=f"generation:{self.run.id}",
                    run_id=self.run.id,
                    timeout=settings.AI_JOB_TIMEOUT_SECONDS,
                ),
            )
        self.course.status = CourseStatus.GENERATING.value
        self.db.commit()
        self.db.refresh(self.run)
        self.run_id = self.run.id

        self.started = time.perf_counter()
        try:
            self._build_graph()
            self._materialize_graph()
            self.run.status = (
                GenerationRunStatus.COMPLETED.value
                if self.prepared_run is not None
                else GenerationRunStatus.SUCCEEDED.value
            )
            self.run.current_stage = "completed"
            self.run.progress_percent = 100
            self.run.finished_at = datetime.utcnow()
            self.run.latency_ms = _elapsed_ms(self.started)
            self.run.output = {
                "graph_id": self.graph.id,
                "graph_version": self.graph.version,
                "node_count": len(self.persisted_nodes),
                "edge_count": len(self.persisted_edges),
                "agentic_pipeline_version": "2.0-langgraph",
                "legacy_fallback": self.build.legacy_fallback,
                "qa": self.build.qa_summary,
                "source_link_count": len(self.link_payloads),
                **self.learning_map,
                **self.materialized,
            }
            from app.services.notifications import generation_finished

            generation_finished(self.db, self.run, self.locked_course)
            self.db.commit()
            self.db.refresh(self.run)
            return self.run
        except Exception as exc:
            error = self._record_graph_failure(exc)
            raise PipelineRunFailed(self.run_id, error, 502) from exc
        finally:
            if self.prepared_run is None:
                from app.services.ai_resources import finish

                finish(
                    self.db,
                    f"generation:{self.run_id}",
                    outcome=ResourceOutcome(
                        failed=self.db.get(GenerationRun, self.run_id).status
                        not in {"succeeded", "completed"}
                    ),
                )
