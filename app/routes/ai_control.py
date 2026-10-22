from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.services.auth_service import require_admin
from app.services import ai_control_service as service


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Default validation includes rejected input, potentially an API key.
                raise HTTPException(422, "Проверьте поля формы: адрес, ключ, тарифы, лимиты и этапы.") from None
        return safe_handler


router = APIRouter(prefix="/admin/ai", route_class=PrivateValidationRoute)


@router.get("")
def overview(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return service.overview(db)


@router.put("/providers/{provider_id}")
def provider(provider_id: str, payload: service.ProviderChange, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return service.update_provider(db, admin, provider_id, payload)


@router.post("/providers/{provider_id}/check")
def check(provider_id: str, payload: service.Revision, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return service.check_provider(db, admin, provider_id, payload)


@router.put("/models")
def model(payload: service.ModelChange, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return service.update_model(db, admin, payload)


@router.put("/policy")
def policy(payload: service.PolicyChange, db: Session = Depends(get_db), admin=Depends(require_admin)):
    if service.settings.AI_LOCAL_ONLY and not payload.local_only:
        raise HTTPException(422, "Локальный режим закреплён в настройках сервера. Отключить его через админку нельзя.")
    row = service.row_for(db, payload.expected_revision)
    return service.save(db, admin, row, "ai.policy.updated", "policy", policy=payload.model_dump(exclude={"expected_revision"}))
