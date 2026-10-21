from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.document import DocumentListItem, document_list_item
from app.schemas.agent_artifact import AgentArtifactOut
from app.schemas.course_update import CourseUpdateProposalList
from app.schemas.generation_run import GenerationRunAccepted, GenerationRunCreate, GenerationRunOut, GenerationRunStatusOut
from app.services.course_generation_settings_service import CourseGenerationSettingsService
from app.services.job_queue import enqueue_generation
from app.repositories.pipeline import PipelineRepository
from app.services.auth_service import get_current_user
from app.services.file_storage import FileStorage, get_file_storage
from app.services.pipeline_service import PipelineRunFailed, PipelineService
from app.services.course_update_service import CourseUpdateService


router = APIRouter()


@router.post("/generation-runs/{run_id}/cancel", response_model=GenerationRunStatusOut)
def cancel_generation(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = PipelineService.get_run(db, run_id, current_user.id)
    course = PipelineRepository.get_owned_course(db, run.course_id, current_user.id, for_update=True)
    db.refresh(run)
    if run.run_type != "graph_generation" or run.status not in {"queued", "running", "paused"}:
        raise HTTPException(409, "Этот запуск уже завершён")
    run.cancel_requested = True
    if run.status != "running":
        run.status, run.error_code, run.retryable = "paused", "user_cancelled", True
        run.error_message = "Генерация остановлена пользователем"
        course.status = "ready" if course.current_graph else "configured"
    db.commit()
    return PipelineService.get_run_status(db, run_id, current_user.id)


@router.post("/courses/{course_id}/generation-preflight")
def preflight_generation(course_id: int, payload: GenerationRunCreate,
                         db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    PipelineService.validate_generation_documents(db, course_id=course_id, owner_id=current_user.id,
                                                 document_ids=payload.document_ids)
    documents = PipelineRepository.selected_indexed_documents(db, course_id, current_user.id, payload.document_ids)
    from app.services.generation_preflight import generation_preflight
    return generation_preflight(documents, payload.settings.model_dump(mode="json"))


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    document = PipelineService.get_document(db, document_id, current_user.id)
    course = PipelineRepository.get_owned_course(db, document.course_id, current_user.id, for_update=True)
    if PipelineRepository.active_graph_run(db, document.course_id, current_user.id):
        raise HTTPException(409, "Остановите генерацию перед удалением источника")
    # Preserve historical files/chunks for immutable publications and their audit.
    # Live retrieval excludes archived documents; caches are invalidated now.
    document.is_deleted, document.is_current, document.status = True, False, "archived"
    if document.learner_visible:
        course.content_revision += 1
    from app.models.ai_state import AIResponseCache, ChatMemory
    from app.models.chat import Chat
    db.query(AIResponseCache).filter_by(course_id=document.course_id).delete(synchronize_session=False)
    chat_ids = db.query(Chat.id).filter_by(course_id=document.course_id, owner_id=current_user.id)
    db.query(ChatMemory).filter(ChatMemory.chat_id.in_(chat_ids)).delete(synchronize_session=False)
    from app.services.embedding_service import remove_document_embeddings
    remove_document_embeddings(document.id)
    db.commit()


@router.post("/courses/{course_id}/generation-runs", response_model=GenerationRunAccepted, status_code=202)
def launch_generation(
    course_id: int,
    payload: GenerationRunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import select
    from app.models.generation_run import GenerationRun
    if PipelineRepository.get_owned_course(db, course_id, current_user.id) is None:
        raise HTTPException(404, "Курс не найден")
    db.execute(select(User).where(User.id == current_user.id).with_for_update()).scalar_one()
    if payload.request_id:
        previous = db.query(GenerationRun).filter_by(owner_id=current_user.id, request_id=payload.request_id).first()
        if previous:
            previous_settings = {key: value for key, value in previous.settings_snapshot.items() if key not in {"settings_revision", "organization_context"}}
            if (previous.course_id != course_id or previous_settings != payload.settings.model_dump(mode="json", exclude={"expected_revision"})
                    or {item["document_id"] for item in previous.input_documents_snapshot} != set(payload.document_ids)):
                raise HTTPException(409, {"code": "idempotency_conflict", "message": "Ключ запуска уже использован с другими параметрами"})
            return GenerationRunAccepted(run_id=previous.id, course_id=course_id, status=previous.status,
                                         status_url=f"/api/generation-runs/{previous.id}")
    active = PipelineRepository.active_graph_run(db, course_id, current_user.id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "generation_run_active",
                "message": "Генерация курса уже запущена",
                "run_id": active.id,
            },
        )
    PipelineService.validate_generation_documents(
        db, course_id=course_id, owner_id=current_user.id,
        document_ids=payload.document_ids,
    )
    CourseGenerationSettingsService.upsert(
        db, course_id, current_user.id, payload.settings, commit=False
    )
    run = PipelineService.prepare_graph_generation(
        db, course_id=course_id, owner_id=current_user.id, document_ids=payload.document_ids, request_id=payload.request_id
    )
    try:
        enqueue_generation(run.id)
    except Exception:
        PipelineService.pause_enqueue(db, run.id)
    return GenerationRunAccepted(
        run_id=run.id, course_id=course_id, status=run.status,
        status_url=f"/api/generation-runs/{run.id}",
    )


def _translate_failure(exc: PipelineRunFailed) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"message": exc.message, "run_id": exc.run_id},
    )


@router.get("/documents/{document_id}", response_model=DocumentListItem)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return document_list_item(
        PipelineService.get_document(db, document_id, current_user.id)
    )


@router.get("/courses/{course_id}/sources/{chunk_id}")
def source_fragment(course_id: int, chunk_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if PipelineRepository.get_owned_course(db, course_id, current_user.id) is None:
        raise HTTPException(404, "Курс не найден")
    from app.models.document import Document, DocumentChunk
    chunk = db.query(DocumentChunk).join(Document).filter(DocumentChunk.id == chunk_id,
        Document.course_id == course_id, Document.is_deleted.is_(False), Document.is_current.is_(True),
        Document.status == "indexed", DocumentChunk.is_deleted.is_(False)).first()
    if chunk is None:
        raise HTTPException(404, "Фрагмент источника недоступен")
    return {"id": chunk.id, "document_id": chunk.document_id, "document_version": chunk.document_version,
            "filename": chunk.document.original_filename, "page": chunk.page, "section": chunk.section, "text": chunk.text}


@router.post(
    "/documents/{document_id}/reindex", response_model=GenerationRunOut
)
def reindex_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage: FileStorage = Depends(get_file_storage),
):
    try:
        return PipelineService.reindex_document(
            db,
            document_id=document_id,
            owner_id=current_user.id,
            storage=storage,
        )
    except PipelineRunFailed as exc:
        raise _translate_failure(exc)


@router.post(
    "/courses/{course_id}/generate-graph", response_model=GenerationRunOut
)
def generate_graph(
    course_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return PipelineService.generate_graph(
            db, course_id=course_id, owner_id=current_user.id, force=force
        )
    except PipelineRunFailed as exc:
        from app.ai.providers import AIUnavailable
        if isinstance(exc.__cause__, AIUnavailable):
            return PipelineService.get_run(db, exc.run_id, current_user.id)
        raise _translate_failure(exc)


@router.get("/generation-runs/{run_id}", response_model=GenerationRunStatusOut)
def get_generation_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return PipelineService.get_run_status(db, run_id, current_user.id)


@router.get(
    "/generation-runs/{run_id}/artifacts",
    response_model=list[AgentArtifactOut],
)
def get_generation_run_artifacts(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return PipelineService.get_run_artifacts(db, run_id, current_user.id)


@router.get(
    "/courses/{course_id}/update-proposals",
    response_model=CourseUpdateProposalList,
)
def list_course_update_proposals(
    course_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = CourseUpdateService.list_proposals(
        db,
        course_id=course_id,
        owner_id=current_user.id,
        limit=limit,
        offset=offset,
    )
    return CourseUpdateProposalList(
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "/generation-runs/{run_id}/retry",
    response_model=GenerationRunAccepted,
    status_code=202,
)
def retry_generation_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = PipelineService.retry_graph_generation(db, run_id, current_user.id)
    try:
        enqueue_generation(run.id)
    except Exception:
        PipelineService.pause_enqueue(db, run.id)
    return GenerationRunAccepted(
        run_id=run.id,
        course_id=run.course_id,
        status=run.status,
        status_url=f"/api/generation-runs/{run.id}",
    )
