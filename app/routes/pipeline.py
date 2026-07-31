from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.document import DocumentListItem, document_list_item
from app.schemas.generation_run import GenerationRunOut
from app.services.auth_service import get_current_user
from app.services.file_storage import FileStorage, get_file_storage
from app.services.pipeline_service import PipelineRunFailed, PipelineService


router = APIRouter()


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


@router.get("/generation-runs/{run_id}", response_model=GenerationRunOut)
def get_generation_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return PipelineService.get_run(db, run_id, current_user.id)
