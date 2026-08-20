from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.course_graph import CourseGraph
from app.models.course import Course
from app.models.course_source_link import CourseSourceLink
from app.models.document import Document
from app.models.domain_enums import (
    CourseGraphStatus,
    DocumentStatus,
    GenerationRunStatus,
    GenerationRunType,
    CourseStatus,
)
from app.models.generation_run import GenerationRun
from app.repositories.pipeline import PipelineRepository
from app.schemas.pipeline import GeneratedGraphPayload
from app.services.document_processing import chunk_blocks, extract_blocks
from app.services.agent_runtime import AgentRuntime
from app.services.agentic_course_pipeline import AgenticCoursePipeline
from app.services.embedding_service import replace_document_embeddings
from app.services.file_storage import FileStorage
from app.services.generation_service import DEFAULT_ENGINE, generate_from_prompt
from app.services.course_generation_settings_service import (
    generation_settings_snapshot,
    settings_not_found,
)
from app.services.course_materialization_service import CourseMaterializationService
from app.services.course_update_service import CourseUpdateService
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
    message = str(exc).strip() or exc.__class__.__name__
    return message[:4000]


def _safe_document_error(exc: Exception) -> str:
    if isinstance(exc, (ValueError, UnicodeError)):
        return "Не удалось извлечь текст из документа"
    return "Не удалось обработать документ"


def _unique_node_id(existing: set[str], base: str) -> str:
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    existing.add(candidate)
    return candidate


def _apply_assessment_settings(
    nodes: list[dict], edges: list[dict], snapshot: dict
) -> tuple[list[dict], list[dict]]:
    existing = {str(node["id"]) for node in nodes}
    modules = [node for node in nodes if node.get("type") == "module"]
    language = snapshot["language"]

    if snapshot["module_tests_enabled"]:
        for index, module in enumerate(modules, start=1):
            test_id = _unique_node_id(existing, f"module-test-{module['id']}")
            nodes.append(
                {
                    "id": test_id,
                    "label": (
                        f"Тест модуля {index}"
                        if language == "ru"
                        else f"Module {index} test"
                    ),
                    "type": "test",
                    "assessment_scope": "module",
                }
            )
            edges.append(
                {"source": module["id"], "target": test_id, "relation": "contains"}
            )

    if snapshot["final_test_enabled"]:
        final_id = _unique_node_id(existing, "final-test")
        nodes.append(
            {
                "id": final_id,
                "label": "Итоговый тест" if language == "ru" else "Final test",
                "type": "test",
                "assessment_scope": "final",
            }
        )
        for module in modules:
            edges.append(
                {"source": module["id"], "target": final_id, "relation": "precedes"}
            )
    return nodes, edges


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
        run.current_stage = stage
        run.progress_percent = progress
        db.commit()

    @staticmethod
    def validate_generation_documents(
        db: Session, *, course_id: int, owner_id: int, document_ids: list[int]
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
        db: Session, *, course_id: int, owner_id: int, document_ids: list[int]
    ) -> GenerationRun:
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
                detail={"code": "generation_run_active", "message": "Генерация курса уже запущена", "run_id": active.id},
            )
        selected_ids = list(dict.fromkeys(document_ids))
        documents = PipelineRepository.selected_indexed_documents(db, course_id, owner_id, selected_ids)
        if len(documents) != len(selected_ids):
            raise HTTPException(
                status_code=422,
                detail={"code": "documents_not_ready", "message": "Документы не найдены, не принадлежат курсу или ещё не проиндексированы"},
            )
        settings_snapshot = generation_settings_snapshot(course, generation_settings)
        docs_snapshot = [
            {"document_id": item.id, "version": item.version, "content_hash": item.content_hash}
            for item in documents
        ]
        fingerprint = hashlib.sha256(
            (
                json.dumps(docs_snapshot, sort_keys=True)
                + json.dumps(settings_snapshot, sort_keys=True)
                + "agentic-pipeline-v1"
            ).encode()
        ).hexdigest()
        now = datetime.utcnow()
        run = GenerationRun(
            owner_id=owner_id,
            course_id=course_id,
            run_type=GenerationRunType.GRAPH_GENERATION.value,
            status=GenerationRunStatus.QUEUED.value,
            current_stage="queued",
            progress_percent=0,
            queued_at=now,
            prompt="agentic-pipeline-v1",
            model=DEFAULT_ENGINE,
            input_docs=docs_snapshot,
            input_documents_snapshot=docs_snapshot,
            settings_snapshot=settings_snapshot,
            input_fingerprint=fingerprint,
        )
        db.add(run)
        course.status = CourseStatus.GENERATING.value
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            active = PipelineRepository.active_graph_run(db, course_id, owner_id)
            if active is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "generation_run_active", "message": "Генерация курса уже запущена", "run_id": active.id},
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
        data = {column.name: getattr(run, column.name) for column in run.__table__.columns}
        data["run_id"] = run.id
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
                data["stages"].append(
                    {"code": code, "title": title, "status": stage_status}
                )
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
        artifacts = PipelineRepository.list_agent_artifacts(
            db, run_id=run_id, owner_id=owner_id
        )
        if artifacts is None:
            raise HTTPException(status_code=404, detail="Запуск не найден")
        return artifacts

    @staticmethod
    def retry_graph_generation(
        db: Session, run_id: int, owner_id: int
    ) -> GenerationRun:
        original = PipelineService.get_run(db, run_id, owner_id)
        if (
            original.run_type != GenerationRunType.GRAPH_GENERATION.value
            or original.status != GenerationRunStatus.FAILED.value
            or not original.retryable
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "generation_run_not_retryable", "message": "Запуск нельзя повторить"},
            )
        active = PipelineRepository.active_graph_run(db, original.course_id, owner_id)
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "generation_run_active", "message": "Генерация курса уже запущена", "run_id": active.id},
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
                detail={"code": "retry_input_unavailable", "message": "Исходные версии документов недоступны"},
            )
        course = PipelineRepository.get_owned_course(
            db, original.course_id, owner_id, for_update=True
        )
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        retry = GenerationRun(
            owner_id=owner_id,
            course_id=original.course_id,
            run_type=original.run_type,
            status=GenerationRunStatus.QUEUED.value,
            current_stage="queued",
            progress_percent=0,
            queued_at=datetime.utcnow(),
            prompt=original.prompt,
            model=original.model,
            input_docs=original.input_docs,
            input_documents_snapshot=original.input_documents_snapshot,
            settings_snapshot=original.settings_snapshot,
            input_fingerprint=original.input_fingerprint,
            attempt=original.attempt + 1,
            retry_of_run_id=original.id,
        )
        course.status = CourseStatus.GENERATING.value
        db.add(retry)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            active = PipelineRepository.active_graph_run(
                db, original.course_id, owner_id
            )
            if active is not None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "generation_run_active", "message": "Генерация курса уже запущена", "run_id": active.id},
                ) from exc
            raise
        db.refresh(retry)
        return retry

    @staticmethod
    def reindex_document(
        db: Session,
        *,
        document_id: int,
        owner_id: int,
        storage: FileStorage,
    ) -> GenerationRun:
        document = PipelineRepository.get_owned_document(db, document_id, owner_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден")
        if document.status == DocumentStatus.ARCHIVED.value:
            raise HTTPException(status_code=409, detail="Документ архивирован")

        fingerprint = hashlib.sha256(
            f"{document.id}:{document.version}:{document.content_hash}".encode()
        ).hexdigest()
        input_docs = [
            {
                "document_id": document.id,
                "version": document.version,
                "content_hash": document.content_hash,
            }
        ]
        run = GenerationRun(
            owner_id=owner_id,
            course_id=document.course_id,
            document_id=document.id,
            run_type=GenerationRunType.DOCUMENT_INDEX.value,
            status=GenerationRunStatus.QUEUED.value,
            input_docs=input_docs,
            input_fingerprint=fingerprint,
        )
        document.status = DocumentStatus.QUEUED.value
        document.processing_error = None
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        started = time.perf_counter()
        try:
            run.status = GenerationRunStatus.RUNNING.value
            document.status = DocumentStatus.PROCESSING.value
            db.commit()

            content = storage.read_bytes(document.storage_key)
            blocks = extract_blocks(content, document.mime_type)
            chunks = chunk_blocks(
                blocks,
                max_chars=settings.DOCUMENT_CHUNK_CHARS,
                overlap_chars=settings.DOCUMENT_CHUNK_OVERLAP_CHARS,
            )
            if not chunks:
                raise ValueError("В документе не найден текст для индексации")

            chunk_models = PipelineRepository.replace_chunks(db, document, chunks)
            vector_chunks = [
                {
                    "chunk_id": chunk_model.id,
                    "chunk_index": chunk_model.chunk_index,
                    "text": chunk_model.text,
                    "page": chunk_model.page,
                    "section": chunk_model.section,
                    "source": document.original_filename,
                    "source_type": document.source_type,
                    "owner_id": document.owner_id,
                    "organization_id": None,
                    "course_id": document.course_id,
                }
                for chunk_model in chunk_models
            ]
            embedding_ids = replace_document_embeddings(
                document.id, document.version, vector_chunks
            )
            for chunk_model, embedding_id in zip(
                chunk_models, embedding_ids, strict=True
            ):
                chunk_model.embedding_id = embedding_id

            if document.supersedes_document_id is not None:
                previous = db.get(Document, document.supersedes_document_id)
                if previous is None or previous.document_key != document.document_key:
                    raise ValueError("Некорректная цепочка версий документа")
                previous.is_current = False
                db.flush()
                document.is_current = True

            document.status = DocumentStatus.INDEXED.value
            document.processing_error = None
            run.status = GenerationRunStatus.SUCCEEDED.value
            run.latency_ms = _elapsed_ms(started)
            run.output = {
                "document_id": document.id,
                "document_version": document.version,
                "chunk_count": len(chunk_models),
                "embedding_count": len(embedding_ids),
            }
            db.commit()
            if document.supersedes_document_id is not None:
                try:
                    proposal = CourseUpdateService.analyze_replacement(
                        db,
                        document=document,
                        run=run,
                        generate=generate_from_prompt,
                    )
                    if proposal is not None:
                        run.output = {
                            **(run.output or {}),
                            "update_proposal_id": proposal.id,
                        }
                        db.commit()
                except Exception as update_exc:
                    db.rollback()
                    logger.warning(
                        "Update impact analysis failed run_id=%s error=%s",
                        run.id,
                        update_exc.__class__.__name__,
                    )
            db.refresh(run)
            return run
        except Exception as exc:
            db.rollback()
            error = _safe_error(exc)
            failed_run = db.get(GenerationRun, run_id)
            failed_document = PipelineRepository.get_owned_document(
                db, document_id, owner_id
            )
            if failed_run is not None:
                failed_run.status = GenerationRunStatus.FAILED.value
                failed_run.error = error
                failed_run.latency_ms = _elapsed_ms(started)
            if failed_document is not None:
                failed_document.status = DocumentStatus.FAILED.value
                failed_document.processing_error = _safe_document_error(exc)
            db.commit()
            status_code = 422 if isinstance(exc, (ValueError, UnicodeError)) else 503
            raise PipelineRunFailed(run_id, error, status_code) from exc

    @staticmethod
    def generate_graph(
        db: Session, *, course_id: int, owner_id: int, force: bool, prepared_run_id: int | None = None
    ) -> GenerationRun:
        course = PipelineRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        prepared_run = db.get(GenerationRun, prepared_run_id) if prepared_run_id is not None else None
        generation_settings = PipelineRepository.get_generation_settings(db, course_id)
        if generation_settings is None:
            raise settings_not_found()
        if course.status == CourseStatus.GENERATING.value and prepared_run is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "course_generation_in_progress",
                    "message": "Генерация курса уже выполняется",
                },
            )
        if prepared_run is None and course.status not in {
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
        settings_snapshot = prepared_run.settings_snapshot if prepared_run is not None else generation_settings_snapshot(course, generation_settings)
        if prepared_run is not None:
            documents = PipelineRepository.documents_for_snapshot(
                db,
                course_id,
                owner_id,
                prepared_run.input_documents_snapshot,
            )
        else:
            documents = PipelineRepository.indexed_documents(db, course_id, owner_id)
        if not documents:
            raise HTTPException(
                status_code=409, detail="У курса нет проиндексированных документов"
            )

        input_docs = [
            {
                "document_id": document.id,
                "version": document.version,
                "content_hash": document.content_hash,
            }
            for document in documents
        ]
        documents_fingerprint = "|".join(
            f"{item['document_id']}:{item['version']}:{item['content_hash']}"
            for item in input_docs
        )
        fingerprint_source = (
            documents_fingerprint
            + "|"
            + json.dumps(settings_snapshot, ensure_ascii=False, sort_keys=True)
            + "|agentic-pipeline-v1"
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
        if not force:
            existing = PipelineRepository.latest_successful_graph_run(
                db, owner_id, course_id, fingerprint
            )
            graph_id = (existing.output or {}).get("graph_id") if existing else None
            if (
                existing is not None
                and isinstance(graph_id, int)
                and PipelineRepository.graph_by_id(db, graph_id) is not None
            ):
                course.status = CourseStatus.READY.value
                db.commit()
                return existing

        run = prepared_run or GenerationRun(
            owner_id=owner_id,
            course_id=course_id,
            run_type=GenerationRunType.GRAPH_GENERATION.value,
            status=GenerationRunStatus.QUEUED.value,
            prompt="agentic-pipeline-v1",
            model=DEFAULT_ENGINE,
            input_docs=input_docs,
            settings_snapshot=settings_snapshot,
            input_fingerprint=fingerprint,
        )
        if prepared_run is None:
            db.add(run)
        course.status = CourseStatus.GENERATING.value
        db.commit()
        db.refresh(run)
        run_id = run.id

        started = time.perf_counter()
        try:
            run.status = GenerationRunStatus.RUNNING.value
            run.started_at = datetime.utcnow()
            source_catalog = build_source_catalog(
                documents, max_chars=settings.GRAPH_CONTEXT_MAX_CHARS
            )
            if not source_catalog:
                raise ValueError("В документах нет доступных фрагментов для генерации")
            runtime = AgentRuntime(
                db=db,
                run_id=run.id,
                course_id=course_id,
                generate=generate_from_prompt,
            )
            pipeline = AgenticCoursePipeline(
                runtime=runtime,
                checkpoint=lambda stage, progress: PipelineService._checkpoint(
                    db, run, stage, progress
                ),
            )
            build = pipeline.run(
                course_title=course.name,
                settings_snapshot=settings_snapshot,
                source_catalog=source_catalog,
            )
            payload = GeneratedGraphPayload.model_validate(
                {"nodes": build.nodes, "edges": build.edges}
            )
            nodes, edges = payload.json_payload()
            generated_lessons = sum(
                node.get("type") == "lesson" for node in nodes
            )
            if generated_lessons != settings_snapshot["lesson_count"]:
                raise ValueError(
                    "Generated lesson count does not match generation settings"
                )
            if build.legacy_fallback:
                nodes, edges = _apply_assessment_settings(
                    nodes, edges, settings_snapshot
                )

            locked_course = PipelineRepository.get_owned_course(
                db, course_id, owner_id, for_update=True
            )
            if locked_course is None:
                raise ValueError("Курс был удалён во время генерации")
            graph = CourseGraph(
                course_id=course_id,
                version=PipelineRepository.next_graph_version(db, course_id),
                nodes=[],
                edges=[],
                created_by=owner_id,
                status=CourseGraphStatus.DRAFT.value,
            )
            if (
                locked_course.current_graph is not None
                and locked_course.current_graph.status == CourseGraphStatus.DRAFT.value
            ):
                locked_course.current_graph.status = CourseGraphStatus.ARCHIVED.value
            db.add(graph)
            db.flush()
            locked_course.current_graph = graph
            materialized = CourseMaterializationService.materialize(
                db, course=locked_course, nodes=nodes, edges=edges
            )
            persisted_nodes = materialized.pop("canvas_nodes")
            persisted_edges = materialized.pop("canvas_edges")
            graph.nodes = persisted_nodes
            graph.edges = persisted_edges
            learning_map = (
                CourseMaterializationService.materialize_learning_map(
                    db, course=locked_course, result=build.result
                )
                if build.result is not None
                else {"competency_count": 0, "learning_objective_count": 0}
            )
            link_payloads = graph_source_links(persisted_nodes, source_catalog)
            PipelineRepository.add_source_links(
                db,
                [
                    CourseSourceLink(
                        course_id=course_id,
                        graph_id=graph.id,
                        run_id=run.id,
                        **item,
                    )
                    for item in link_payloads
                ],
            )
            locked_course.status = CourseStatus.READY.value

            run.status = GenerationRunStatus.COMPLETED.value if prepared_run is not None else GenerationRunStatus.SUCCEEDED.value
            run.current_stage = "completed"
            run.progress_percent = 100
            run.finished_at = datetime.utcnow()
            run.latency_ms = _elapsed_ms(started)
            run.output = {
                "graph_id": graph.id,
                "graph_version": graph.version,
                "node_count": len(persisted_nodes),
                "edge_count": len(persisted_edges),
                "agentic_pipeline_version": "1.0",
                "legacy_fallback": build.legacy_fallback,
                "qa": build.qa_summary,
                "source_link_count": len(link_payloads),
                **learning_map,
                **materialized,
            }
            db.commit()
            db.refresh(run)
            return run
        except Exception as exc:
            db.rollback()
            error = _safe_error(exc)
            failed_run = db.get(GenerationRun, run_id)
            failed_course = PipelineRepository.get_owned_course(
                db, course_id, owner_id, for_update=True
            )
            if failed_run is not None:
                failed_run.status = GenerationRunStatus.FAILED.value
                failed_run.error = error
                failed_run.error_code = "generation_failed"
                failed_run.error_message = "Не удалось сгенерировать курс"
                failed_run.retryable = True
                failed_run.finished_at = datetime.utcnow()
                failed_run.latency_ms = _elapsed_ms(started)
            if failed_course is not None:
                failed_course.status = CourseStatus.GENERATION_FAILED.value
            db.commit()
            raise PipelineRunFailed(run_id, error, 502) from exc
