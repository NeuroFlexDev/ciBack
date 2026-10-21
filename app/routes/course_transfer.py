from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.services.auth_service import get_current_user
from app.services import course_transfer as transfer

router = APIRouter(prefix='/team/course-transfers')


class ExportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    course_version_id: int = Field(gt=0)
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    include_sources: bool = True
    include_answer_keys: bool = False
    confirm_transfer: bool = False
    confirm_private_materials: bool = False


@router.get('')
def list_versions(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    return transfer.catalogue(db, user, offset, limit)


@router.post('/export')
def export_version(payload: ExportInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    archive, filename = transfer.prepare_bundle(db, user, payload)
    def chunks():
        try:
            while block := archive.read(256 * 1024):
                yield block
        finally:
            archive.close()
    return StreamingResponse(chunks(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff'})
