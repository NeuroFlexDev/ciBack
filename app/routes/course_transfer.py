from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.services.auth_service import get_current_user
from app.services import course_transfer as transfer
from app.core.http_security import bounded_json


class BoundedTransferRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            if request.method == 'POST':
                # Enforce before FastAPI parses/allocates arbitrary SVG JSON.
                await bounded_json(request, 9 * 1024 * 1024 if request.url.path.endswith('/export.pdf') else 64 * 1024)
            return await handler(request)
        return bounded


router = APIRouter(prefix='/team/course-transfers', route_class=BoundedTransferRoute)


class ExportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    course_version_id: int = Field(gt=0)
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    include_sources: bool = True
    include_answer_keys: bool = False
    confirm_transfer: bool = False
    confirm_private_materials: bool = False


@router.get('')
def list_versions(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), course_id: int | None = Query(None, gt=0),
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    return transfer.catalogue(db, user, offset, limit, course_id)


class PDFDiagram(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-f0-9]{64}$')
    svg: str = Field(min_length=20, max_length=1500000)


class PDFInput(ExportInput):
    diagrams: list[PDFDiagram] = Field(default_factory=list, max_length=80)

    @model_validator(mode='after')
    def bounded_svg_bytes(self):
        if sum(len(item.svg.encode('utf8')) for item in self.diagrams) > 8 * 1024 * 1024:
            raise ValueError('SVG diagrams exceed 8 MiB in total')
        return self


@router.post('/pdf-plan')
def plan_pdf(payload: ExportInput, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.course_pdf import export_pdf
    return export_pdf(db, user, payload, request.state.auth_claims, plan=True)


@router.post('/export.pdf')
def download_pdf(payload: PDFInput, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    from app.services.course_pdf import export_pdf
    content, filename = export_pdf(db, user, payload, request.state.auth_claims)
    return Response(content, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff'})


@router.post('/export')
def export_version(payload: ExportInput, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    archive, filename = transfer.prepare_bundle(db, user, payload, claims=request.state.auth_claims)
    def chunks():
        try:
            while block := archive.read(256 * 1024):
                yield block
        finally:
            archive.close()
    return StreamingResponse(chunks(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff'})
