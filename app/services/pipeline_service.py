from __future__ import annotations

import hashlib
import time

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.course_graph import CourseGraph
from app.models.domain_enums import (
    CourseGraphStatus,
    DocumentStatus,
    GenerationRunStatus,
    GenerationRunType,
)
from app.models.generation_run import GenerationRun
from app.repositories.pipeline import PipelineRepository
from app.schemas.pipeline import GeneratedGraphPayload
from app.services.document_processing import chunk_blocks, extract_blocks
from app.services.embedding_service import replace_document_embeddings
from app.services.file_storage import FileStorage
from app.services.generation_service import DEFAULT_ENGINE, generate_from_prompt


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


class PipelineService:
    @staticmethod
    def get_document(db: Session, document_id: int, owner_id: int):
        document = PipelineRepository.get_owned_document(db, document_id, owner_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден")
        return document

    @staticmethod
    def get_run(db: Session, run_id: int, owner_id: int) -> GenerationRun:
        run = PipelineRepository.get_owned_run(db, run_id, owner_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Запуск не найден")
        return run

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
            embedding_ids = replace_document_embeddings(
                document.id, document.version, chunks
            )
            for chunk_model, embedding_id in zip(
                chunk_models, embedding_ids, strict=True
            ):
                chunk_model.embedding_id = embedding_id

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
                failed_document.processing_error = error
            db.commit()
            status_code = 422 if isinstance(exc, (ValueError, UnicodeError)) else 503
            raise PipelineRunFailed(run_id, error, status_code) from exc

    @staticmethod
    def generate_graph(
        db: Session, *, course_id: int, owner_id: int, force: bool
    ) -> GenerationRun:
        course = PipelineRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
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
        fingerprint_source = "|".join(
            f"{item['document_id']}:{item['version']}:{item['content_hash']}"
            for item in input_docs
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
                return existing

        run = GenerationRun(
            owner_id=owner_id,
            course_id=course_id,
            run_type=GenerationRunType.GRAPH_GENERATION.value,
            status=GenerationRunStatus.QUEUED.value,
            prompt="course_graph_prompt.j2",
            model=DEFAULT_ENGINE,
            input_docs=input_docs,
            input_fingerprint=fingerprint,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        started = time.perf_counter()
        try:
            run.status = GenerationRunStatus.RUNNING.value
            db.commit()

            context_parts = []
            remaining = settings.GRAPH_CONTEXT_MAX_CHARS
            for document in documents:
                chunks = sorted(document.chunks, key=lambda item: item.chunk_index)
                for chunk in chunks:
                    header = (
                        f"[document={document.id} version={document.version} "
                        f"page={chunk.page or '-'} section={chunk.section or '-'}]\n"
                    )
                    part = f"{header}{chunk.text}\n"
                    if len(part) > remaining:
                        part = part[:remaining]
                    if part:
                        context_parts.append(part)
                        remaining -= len(part)
                    if remaining <= 0:
                        break
                if remaining <= 0:
                    break

            raw_graph = generate_from_prompt(
                "course_graph_prompt.j2",
                include_external_context=False,
                use_feedback=False,
                course_title=course.name,
                document_context="\n".join(context_parts),
            )
            payload = GeneratedGraphPayload.model_validate(raw_graph)
            nodes, edges = payload.json_payload()

            locked_course = PipelineRepository.get_owned_course(
                db, course_id, owner_id, for_update=True
            )
            if locked_course is None:
                raise ValueError("Курс был удалён во время генерации")
            graph = CourseGraph(
                course_id=course_id,
                version=PipelineRepository.next_graph_version(db, course_id),
                nodes=nodes,
                edges=edges,
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

            run.status = GenerationRunStatus.SUCCEEDED.value
            run.latency_ms = _elapsed_ms(started)
            run.output = {
                "graph_id": graph.id,
                "graph_version": graph.version,
                "node_count": len(nodes),
                "edge_count": len(edges),
            }
            db.commit()
            db.refresh(run)
            return run
        except Exception as exc:
            db.rollback()
            error = _safe_error(exc)
            failed_run = db.get(GenerationRun, run_id)
            if failed_run is not None:
                failed_run.status = GenerationRunStatus.FAILED.value
                failed_run.error = error
                failed_run.latency_ms = _elapsed_ms(started)
            db.commit()
            raise PipelineRunFailed(run_id, error, 502) from exc
