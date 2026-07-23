from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.domain_enums import DocumentStatus
from app.models.user import User
from app.schemas.course_graph import CanvasOut, CanvasPut
from app.schemas.document import DocumentListOut, DocumentPublicOut
from app.services.auth_service import get_current_user
from app.services.course_content_service import CourseContentService
from app.services.file_storage import FileStorage, get_file_storage
from app.repositories.course_content import CourseContentRepository


router = APIRouter(prefix="/courses")


@router.get("/{course_id}/canvas", response_model=CanvasOut)
def get_canvas(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return CourseContentService.get_canvas(db, course_id, current_user.id)


@router.put("/{course_id}/canvas", response_model=CanvasOut)
def put_canvas(
    course_id: int,
    payload: CanvasPut,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return CourseContentService.save_canvas(db, course_id, current_user.id, payload)


@router.post(
    "/{course_id}/documents",
    response_model=DocumentPublicOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    course_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage: FileStorage = Depends(get_file_storage),
):
    return CourseContentService.upload_document(
        db,
        course_id=course_id,
        owner_id=current_user.id,
        upload=file,
        storage=storage,
    )


@router.get("/{course_id}/documents", response_model=DocumentListOut)
def list_documents(
    course_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    source_type: str | None = Query(default=None, min_length=1, max_length=64),
    sort_by: Literal[
        "created_at", "original_filename", "size_bytes", "status"
    ] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = CourseContentRepository.get_owned_course(
        db, course_id, current_user.id
    )
    if course is None:
        raise HTTPException(status_code=404, detail="Курс не найден")
    items, total = CourseContentRepository.list_documents(
        db,
        course_id=course_id,
        owner_id=current_user.id,
        limit=limit,
        offset=offset,
        status=document_status.value if document_status else None,
        source_type=source_type,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return DocumentListOut(items=items, total=total, limit=limit, offset=offset)
