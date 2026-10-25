from datetime import datetime
from urllib.parse import urlencode, urlsplit
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import create_access_token
from app.database.db import get_db
from app.models.integrations import OIDCProvider, OIDCSession, ExternalIdentity
from app.models.user import User
from app.schemas.auth import UserResponse
from app.services.auth_service import get_current_user, ensure_organization_active
from app.services.integration_access import integration_owner
from app.services.integration_http import validate_endpoint
from app.services.integration_secrets import seal
from app.services.organization_access import audit
from app.services import oidc_service

router=APIRouter()


class ProviderInput(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    name:str=Field(min_length=2,max_length=160)
    issuer:str=Field(max_length=2048)
    client_id:str=Field(min_length=1,max_length=255)
    client_secret:SecretStr
    authorization_endpoint:str=Field(max_length=2048)
    token_endpoint:str=Field(max_length=2048)
    jwks_uri:str=Field(max_length=2048)
    allow_provisioning:bool=False
    allowed_domains:list[str]=Field(default_factory=list,max_length=100)


@router.get("/integrations/oidc")
def providers(db:Session=Depends(get_db),user=Depends(integration_owner)):
    return {"callback_url":oidc_service.callback_url(),"items":[{key:getattr(row,key) for key in ("id","name","issuer","client_id","authorization_endpoint","token_endpoint","jwks_uri","enabled","allow_provisioning","allowed_domains")} for row in db.query(OIDCProvider).filter_by(organization_id=user.organization_id)]}


@router.post("/integrations/oidc",status_code=201)
def create_provider(payload:ProviderInput,db:Session=Depends(get_db),user=Depends(integration_owner)):
    try:
        for url in (payload.issuer,payload.authorization_endpoint,payload.token_endpoint,payload.jwks_uri):validate_endpoint(url)
    except (ValueError,OSError):raise HTTPException(422,"Проверьте HTTPS-адреса провайдера входа")
    domains=sorted({value.lower().strip() for value in payload.allowed_domains})
    if payload.allow_provisioning and not domains:raise HTTPException(422,"Укажите разрешённые домены для создания участников")
    if any(not d or "@" in d or "/" in d or " " in d for d in domains):raise HTTPException(422,"Некорректный домен почты")
    try:secret=seal(payload.client_secret.get_secret_value())
    except RuntimeError:raise HTTPException(503,"Оператор ещё не настроил шифрование интеграций")
    row=OIDCProvider(organization_id=user.organization_id,client_secret_encrypted=secret,**payload.model_dump(exclude={"client_secret","allowed_domains"}),allowed_domains=domains)
    db.add(row);db.flush();audit(db,user,"oidc.created","oidc_provider",row.id,organization_id=user.organization_id)
    db.commit();return {"id":row.id,"name":row.name,"start_url":settings.PUBLIC_APP_URL.rstrip("/")+f"/api/auth/oidc/{row.id}/start"}


@router.delete("/integrations/oidc/{provider_id}")
def disable_provider(provider_id:int,db:Session=Depends(get_db),user=Depends(integration_owner)):
    row=db.query(OIDCProvider).filter_by(id=provider_id,organization_id=user.organization_id).first()
    if not row:raise HTTPException(404,"Подключение не найдено")
    row.enabled=False;audit(db,user,"oidc.disabled","oidc_provider",row.id,organization_id=user.organization_id)
    db.commit();return {"ok":True}


def start_response(db,provider,link_user_id=None,mobile=False):
    if mobile and not settings.MOBILE_OIDC_REDIRECT_URI:
        raise HTTPException(503,"Мобильный OIDC возврат не настроен оператором")
    if mobile:
        parsed=urlsplit(settings.MOBILE_OIDC_REDIRECT_URI)
        if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise HTTPException(503,"Для мобильного возврата требуется HTTPS Universal Link / App Link")
    url,state,browser=oidc_service.begin(db,provider,link_user_id)
    if mobile:
        row=db.get(OIDCSession,oidc_service.digest(state));row.mobile_handoff=True;db.commit()
    response=RedirectResponse(url,status_code=303)
    response.set_cookie("lernium_oidc_"+state[:12],browser,max_age=600,httponly=True,secure=settings.PUBLIC_APP_URL.startswith("https:"),
        samesite="lax",path="/api/auth/oidc")
    return response


@router.get("/auth/oidc/{provider_id}/start")
def start(provider_id:int,mobile:bool=False,db:Session=Depends(get_db)):
    provider=db.query(OIDCProvider).filter_by(id=provider_id,enabled=True).first()
    if not provider:raise HTTPException(404,"Подключение входа не найдено")
    return start_response(db,provider,mobile=mobile)


@router.post("/auth/oidc/{provider_id}/link")
def link(provider_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    provider=db.query(OIDCProvider).filter_by(id=provider_id,organization_id=user.organization_id,enabled=True).first()
    if not provider:raise HTTPException(404,"Подключение входа не найдено")
    url,state,browser=oidc_service.begin(db,provider,user.id)
    from fastapi.responses import JSONResponse
    response=JSONResponse({"url":url})
    response.set_cookie("lernium_oidc_"+state[:12],browser,max_age=600,httponly=True,secure=settings.PUBLIC_APP_URL.startswith("https:"),samesite="lax",path="/api/auth/oidc")
    return response


@router.get("/auth/oidc/callback")
def callback(request:Request,state:str=Query(min_length=32,max_length=128),code:str|None=Query(None,min_length=1,max_length=4096),error:str|None=Query(None,max_length=255),db:Session=Depends(get_db)):
    session=db.get(OIDCSession,oidc_service.digest(state))
    mobile=bool(session and session.mobile_handoff and settings.MOBILE_OIDC_REDIRECT_URI)
    target=settings.MOBILE_OIDC_REDIRECT_URI if mobile else settings.PUBLIC_APP_URL.rstrip("/")+"/auth"
    if (error or not code) and session and not session.consumed_at:
        session.consumed_at=datetime.utcnow()
        db.commit()
    try:
        if error or not code:raise HTTPException(400,"Вход отменён сервисом учётных записей. Повторите вход или используйте пароль")
        browser=request.cookies.get("lernium_oidc_"+state[:12])
        exchange=oidc_service.complete(db,state,code,browser)
        fragment={"sso_code":exchange,"sso_state":state}
    except HTTPException as exc:
        db.rollback();fragment={"sso_error":str(exc.detail)}
    if mobile:
        return RedirectResponse(target+(("&" if "?" in target else "?")+urlencode(fragment)),status_code=303)
    return RedirectResponse(target+"#"+urlencode(fragment),status_code=303)


class ExchangeInput(BaseModel):
    code:str=Field(min_length=32,max_length=128)
    state:str=Field(min_length=32,max_length=128)


@router.post("/auth/oidc/exchange")
def exchange(payload:ExchangeInput,request:Request,db:Session=Depends(get_db)):
    row=db.get(OIDCSession,oidc_service.digest(payload.state))
    browser=request.cookies.get("lernium_oidc_"+payload.state[:12])
    if not row or not row.exchange_code_hash or row.exchange_code_hash!=oidc_service.digest(payload.code) or row.expires_at<=datetime.utcnow() or row.exchanged_at or (not row.mobile_handoff and (not browser or row.browser_hash!=oidc_service.digest(browser))):
        raise HTTPException(400,"Сеанс входа недействителен. Повторите вход")
    user=db.get(User,row.authenticated_user_id)
    provider=db.get(OIDCProvider,row.provider_id)
    from app.services.integration_access import ensure_integrations_enabled
    ensure_integrations_enabled(db, provider.organization_id)
    if not user or not user.is_active or not provider.enabled or user.organization_id!=provider.organization_id:raise HTTPException(403,"Доступ отозван")
    ensure_organization_active(user)
    claimed=db.execute(update(OIDCSession).where(OIDCSession.state_hash==row.state_hash,OIDCSession.exchanged_at.is_(None)).values(exchanged_at=datetime.utcnow()))
    if claimed.rowcount!=1:raise HTTPException(400,"Сеанс входа уже использован")
    from app.services.mfa_service import authentication_result
    result=authentication_result(db,user,primary='oidc',context={'oidc_provider_id':provider.id,'organization_id':provider.organization_id,**({'mobile':True} if row.mobile_handoff else {})})
    if row.mobile_handoff and not result.get('mfa_required'):
        from app.services.mobile_sessions import issue
        result=issue(db,user,amr=['oidc'])
    db.commit()
    from fastapi.responses import JSONResponse
    from fastapi.encoders import jsonable_encoder
    response=JSONResponse(jsonable_encoder(result))
    response.delete_cookie("lernium_oidc_"+payload.state[:12],path="/api/auth/oidc")
    return response


@router.get("/me/identities")
def identities(db:Session=Depends(get_db),user=Depends(get_current_user)):
    linked={row.provider_id for row in db.query(ExternalIdentity).filter_by(user_id=user.id)}
    return {"items":[{"id":row.id,"name":row.name,"linked":row.id in linked,"enabled":row.enabled} for row in db.query(OIDCProvider).filter_by(organization_id=user.organization_id)]}
