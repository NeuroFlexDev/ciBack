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


@router.post("/courses/{course_id}/generation-runs", response_model=GenerationRunAccepted, status_code=202)
def launch_generation(
    course_id: int,
    payload: GenerationRunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
        db, course_id=course_id, owner_id=current_user.id, document_ids=payload.document_ids
    )
    try:
        enqueue_generation(run.id)
    except Exception:
        PipelineService.fail_enqueue(db, run.id)
        raise HTTPException(status_code=503, detail={"code": "queue_unavailable", "message": "Сервис генерации временно недоступен"})
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
        PipelineService.fail_run(db, run.id, code="queue_unavailable")
        raise HTTPException(
            status_code=503,
            detail={"code": "queue_unavailable", "message": "Сервис генерации временно недоступен"},
        )
    return GenerationRunAccepted(
        run_id=run.id,
        course_id=run.course_id,
        status=run.status,
        status_url=f"/api/generation-runs/{run.id}",
    )
