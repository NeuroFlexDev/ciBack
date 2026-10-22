import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.integrations import IntegrationKey, IntegrationOperation, WebhookEndpoint, OutboundMessage
from app.models.course import Course
from app.models.user import User
from app.services.integration_access import SCOPES, integration_owner, require_scope
from app.services.integration_http import validate_endpoint
from app.services.integration_secrets import seal, unseal
from app.services.organization_access import audit, member_ids, lock_org, lock_member
from app.services.tenant_lifecycle import WEBHOOK_FIELDS, state_token, require_state
from app.services.outbox_service import EVENT_TYPES

router = APIRouter()


@router.get("/v1/openapi.json",include_in_schema=False)
def integration_contract(request:Request):
    schema=request.app.openapi()
    return schema|{"info":{"title":"Lernium customer integration API","version":"1.0.0",
        "description":"Tenant-scoped integration keys only. Offset pagination: offset>=0, 1<=limit<=100. Mutation external_id is immutable within an organization. Errors use detail and X-Request-ID; 401 invalid/expired credentials, 403 scope/access, 404 tenant-scoped missing object, 409 idempotency or lifecycle conflict, 422 validation, 429 retry-after, 503 temporary dependency failure."},
        "paths":{path:value for path,value in schema["paths"].items() if path.startswith("/api/v1/")}}


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=160)
    scopes: list[str] = Field(min_length=1, max_length=10)
    expires_at: datetime

    @field_validator("scopes")
    @classmethod
    def scopes_valid(cls, value):
        if not set(value) <= SCOPES:
            raise ValueError("Unknown integration scope")
        return sorted(set(value))

    @field_validator("expires_at")
    @classmethod
    def expiry_valid(cls, value):
        value = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        if not datetime.utcnow() < value <= datetime.utcnow()+timedelta(days=366):
            raise ValueError("Expiry must be within one year")
        return value


def key_dict(row):
    return {key:getattr(row,key) for key in ("id","name","prefix","scopes","expires_at","revoked_at","last_used_at","created_at")}


def make_key(db, user, name, scopes, expires_at):
    raw = "lrn_" + secrets.token_urlsafe(36)
    row = IntegrationKey(organization_id=user.organization_id, name=name, prefix=raw[:12],
        key_hash=hashlib.sha256(raw.encode()).hexdigest(), scopes=scopes, expires_at=expires_at, created_by=user.id)
    db.add(row); db.flush()
    audit(db,user,"integration_key.created","integration_key",row.id,organization_id=user.organization_id,scopes=scopes)
    return row, raw


@router.get("/integrations/keys")
def keys(db:Session=Depends(get_db),user=Depends(integration_owner)):
    return {"scopes":sorted(SCOPES),"items":[key_dict(row) for row in db.query(IntegrationKey).filter_by(organization_id=user.organization_id).order_by(IntegrationKey.id.desc())]}


@router.post("/integrations/keys",status_code=201)
def create_key(payload:KeyInput,db:Session=Depends(get_db),user=Depends(integration_owner)):
    row, raw = make_key(db,user,payload.name,payload.scopes,payload.expires_at)
    db.commit();return key_dict(row)|{"key":raw}


@router.delete("/integrations/keys/{key_id}")
def revoke_key(key_id:int,db:Session=Depends(get_db),user=Depends(integration_owner)):
    row=db.query(IntegrationKey).filter_by(id=key_id,organization_id=user.organization_id).with_for_update().first()
    if not row:raise HTTPException(404,"Ключ не найден")
    if not row.revoked_at:
        row.revoked_at=datetime.utcnow();audit(db,user,"integration_key.revoked","integration_key",row.id,organization_id=user.organization_id)
    db.commit();return {"ok":True}


@router.post("/integrations/keys/{key_id}/rotate",status_code=201)
def rotate_key(key_id:int,db:Session=Depends(get_db),user=Depends(integration_owner)):
    row=db.query(IntegrationKey).filter_by(id=key_id,organization_id=user.organization_id).with_for_update().first()
    if not row:raise HTTPException(404,"Ключ не найден")
    if row.revoked_at or row.expires_at<=datetime.utcnow():raise HTTPException(409,"Создайте новый ключ: этот отозван или истёк")
    row.revoked_at=datetime.utcnow()
    replacement,raw=make_key(db,user,row.name,row.scopes,row.expires_at)
    audit(db,user,"integration_key.rotated","integration_key",row.id,replacement_id=replacement.id,organization_id=user.organization_id)
    db.commit();return key_dict(replacement)|{"key":raw}


class WebhookInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    url:str=Field(max_length=2048)
    events:list[str]=Field(min_length=1,max_length=20)
    @field_validator("events")
    @classmethod
    def events_valid(cls,value):
        if not set(value)<=EVENT_TYPES:raise ValueError("Unknown event type")
        return sorted(set(value))


class WebhookUpdate(WebhookInput):
    expected_state: str | None = Field(default=None, min_length=64, max_length=64)
    enabled: bool


def webhook_out(row):
    return {**{key: getattr(row, key) for key in ('id', 'url', 'events', 'enabled', 'created_at')},
            'state_token': state_token(row, WEBHOOK_FIELDS)}


def lock_webhook(db, user, endpoint_id):
    from app.services.learning_service import require_manager
    actor = lock_member(db, user.id, expected_auth_version=user.auth_version)
    org_id = require_manager(db, actor)
    row = db.query(WebhookEndpoint).filter_by(id=endpoint_id, organization_id=org_id).with_for_update().populate_existing().first()
    if not row:
        raise HTTPException(404, 'Подключение не найдено')
    return row


@router.get("/integrations/webhooks")
def webhooks(db:Session=Depends(get_db),user=Depends(integration_owner)):
    return {"events":sorted(EVENT_TYPES),"items":[webhook_out(row) for row in db.query(WebhookEndpoint).filter_by(organization_id=user.organization_id).order_by(WebhookEndpoint.id.desc())]}


@router.post("/integrations/webhooks",status_code=201)
def create_webhook(payload:WebhookInput,db:Session=Depends(get_db),user=Depends(integration_owner)):
    try:validate_endpoint(payload.url)
    except (ValueError,OSError):raise HTTPException(422,"Укажите доступный HTTPS-адрес внешнего сервиса")
    secret=secrets.token_urlsafe(32)
    try:encrypted=seal(secret)
    except RuntimeError:raise HTTPException(503,"Оператор ещё не настроил шифрование интеграций")
    row=WebhookEndpoint(organization_id=user.organization_id,url=payload.url,events=payload.events,secret_encrypted=encrypted)
    db.add(row);db.flush();audit(db,user,"webhook.created","webhook",row.id,organization_id=user.organization_id)
    db.commit();return {**webhook_out(row), "secret": secret}


@router.put('/integrations/webhooks/{endpoint_id}')
def update_webhook(endpoint_id: int, payload: WebhookUpdate, db: Session = Depends(get_db), user=Depends(integration_owner)):
    row = lock_webhook(db, user, endpoint_id)
    require_state(row, WEBHOOK_FIELDS, payload.expected_state)
    try:
        validate_endpoint(payload.url)
    except (ValueError, OSError):
        raise HTTPException(422, 'Укажите доступный HTTPS-адрес внешнего сервиса')
    # Legacy messages referenced a mutable endpoint. Pin their original address
    # and signature key before editing so queued/retried events cannot move to
    # a different recipient. New messages already carry this encrypted snapshot.
    pending = db.query(OutboundMessage).filter(
        OutboundMessage.organization_id == row.organization_id,
        OutboundMessage.kind == 'webhook',
        OutboundMessage.deduplication_key.startswith(f'webhook:{row.id}:'),
        OutboundMessage.status.in_(['pending', 'sending', 'failed'])).with_for_update().all()
    for message in pending:
        saved = unseal(message.payload_encrypted)
        if saved.get('endpoint_id') != row.id:
            raise HTTPException(409, 'Не удалось проверить исходного получателя ожидающей доставки')
        if 'endpoint_url' not in saved:
            message.payload_encrypted = seal({**saved, 'endpoint_url': row.url,
                                               'endpoint_secret': unseal(row.secret_encrypted)})
    row.url, row.events, row.enabled = payload.url, payload.events, payload.enabled
    audit(db, user, 'webhook.updated', 'webhook', row.id, organization_id=row.organization_id,
          events=payload.events, enabled=payload.enabled, existing_delivery_destinations_preserved=True)
    db.commit()
    return webhook_out(row)


@router.delete("/integrations/webhooks/{endpoint_id}")
def disable_webhook(endpoint_id:int,expected_state: str | None = Query(default=None, min_length=64, max_length=64),db:Session=Depends(get_db),user=Depends(integration_owner)):
    row=lock_webhook(db,user,endpoint_id)
    require_state(row, WEBHOOK_FIELDS, expected_state)
    row.enabled=False;audit(db,user,"webhook.disabled","webhook",row.id,organization_id=user.organization_id)
    db.commit();return {"ok":True}


@router.get("/integrations/deliveries")
def deliveries(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),user=Depends(integration_owner)):
    query=db.query(OutboundMessage).filter(OutboundMessage.organization_id==user.organization_id,OutboundMessage.kind!="mail")
    return {"total":query.count(),"items":[{key:getattr(row,key) for key in ("id","kind","status","attempts","available_at","delivered_at","last_error","created_at")} for row in query.order_by(OutboundMessage.created_at.desc()).offset(offset).limit(limit)]}


@router.post("/integrations/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id:str,db:Session=Depends(get_db),user=Depends(integration_owner)):
    row=db.query(OutboundMessage).filter(OutboundMessage.id==delivery_id,OutboundMessage.organization_id==user.organization_id,OutboundMessage.kind!="mail").with_for_update().first()
    if not row:raise HTTPException(404,"Доставка не найдена")
    if row.status!="failed":raise HTTPException(409,"Повтор доступен для завершившихся с ошибкой доставок")
    row.status="pending";row.attempts=0;row.available_at=datetime.utcnow()
    audit(db,user,"delivery.retried","delivery",row.id,organization_id=user.organization_id)
    db.commit();return {"ok":True}


@router.get("/v1/courses")
def api_courses(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),principal=Depends(require_scope("courses:read"))):
    tenant_filter=Course.organization_id==principal.organization_id if hasattr(Course,"organization_id") else Course.owner_id.in_(member_ids(db,principal.organization_id))
    query=db.query(Course).filter(tenant_filter,Course.is_deleted.is_(False))
    return {"version":"1","total":query.count(),"offset":offset,"limit":limit,
        "items":[{key:getattr(row,key) for key in ("id","name","description","status","publication_status","published_at","content_revision")} for row in query.order_by(Course.id).offset(offset).limit(limit)]}


@router.get("/v1/members")
def api_members(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),principal=Depends(require_scope("members:read"))):
    query=db.query(User).filter_by(organization_id=principal.organization_id)
    return {"version":"1","total":query.count(),"offset":offset,"limit":limit,
        "items":[{key:getattr(row,key) for key in ("id","full_name","email","organization_role","is_active")} for row in query.order_by(User.id).offset(offset).limit(limit)]}


def assignment_page(db,org_id,offset,limit,user_id=None):
    from app.models.learning import LearningAssignment
    from app.services.learning_service import assignment_summary
    query=db.query(LearningAssignment).filter_by(organization_id=org_id)
    if user_id:query=query.filter_by(user_id=user_id)
    return {"version":"1","total":query.count(),"offset":offset,"limit":limit,
        "items":[assignment_summary(db,row) for row in query.order_by(LearningAssignment.id).offset(offset).limit(limit)]}


@router.get("/v1/assignments")
def api_assignments(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),user_id:int|None=Query(None,gt=0),
                    db:Session=Depends(get_db),principal=Depends(require_scope("assignments:read"))):
    return assignment_page(db,principal.organization_id,offset,limit,user_id)


@router.get("/v1/results")
def api_results(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),user_id:int|None=Query(None,gt=0),
                db:Session=Depends(get_db),principal=Depends(require_scope("results:read"))):
    return assignment_page(db,principal.organization_id,offset,limit,user_id)


class AssignmentInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    course_version_id:int=Field(gt=0)
    user_id:int=Field(gt=0)
    external_id:str=Field(min_length=1,max_length=64,pattern=r"^[A-Za-z0-9_:.\-]+$")


@router.post("/v1/assignments")
def api_assign(payload:AssignmentInput,db:Session=Depends(get_db),principal=Depends(require_scope("assignments:write"))):
    from app.services.learning_service import assign_from_integration, assignment_summary
    from app.models.learning import LearningAssignment
    lock_org(db,principal.organization_id)
    request_hash=hashlib.sha256(json.dumps(payload.model_dump(),sort_keys=True).encode()).hexdigest()
    previous=db.query(IntegrationOperation).filter_by(organization_id=principal.organization_id,external_id=payload.external_id).first()
    if previous:
        if previous.request_hash!=request_hash:raise HTTPException(409,"external_id уже использован с другими параметрами")
        return {"version":"1","created":False,"assignment":assignment_summary(db,db.get(LearningAssignment,previous.assignment_id))}
    row,created=assign_from_integration(db,org_id=principal.organization_id,version_id=payload.course_version_id,user_id=payload.user_id,
        actor_id=principal.created_by,cycle_key=payload.external_id)
    db.add(IntegrationOperation(organization_id=principal.organization_id,external_id=payload.external_id,request_hash=request_hash,assignment_id=row.id))
    db.commit();return {"version":"1","created":created,"assignment":assignment_summary(db,row)}


@router.delete("/v1/assignments/{assignment_id}")
def api_revoke_assignment(assignment_id:int,db:Session=Depends(get_db),principal=Depends(require_scope("assignments:write"))):
    from app.services.learning_service import revoke_from_integration, assignment_summary
    row=revoke_from_integration(db,org_id=principal.organization_id,assignment_id=assignment_id,actor_id=principal.created_by)
    db.commit();return {"version":"1","assignment":assignment_summary(db,row)}
