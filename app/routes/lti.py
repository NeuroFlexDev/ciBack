import json
import secrets
import uuid
from datetime import datetime
from urllib.parse import urlencode
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import create_access_token
from app.database.db import get_db
from app.models.integrations import LTIPlatform, LTIResource, LTIIdentity, LTISession
from app.models.user import User
from app.services.integration_access import integration_owner
from app.services.integration_http import validate_endpoint
from app.services.integration_secrets import seal
from app.services.organization_access import audit
from app.services.auth_service import ensure_organization_active
from app.services.oidc_service import digest
from app.services import lti_service
from app.schemas.auth import UserResponse

router=APIRouter()


class PlatformInput(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    name:str=Field(min_length=2,max_length=160)
    issuer:str=Field(max_length=2048)
    client_id:str=Field(min_length=1,max_length=255)
    deployment_id:str=Field(min_length=1,max_length=255)
    authorization_endpoint:str=Field(max_length=2048)
    token_endpoint:str=Field(max_length=2048)
    jwks_uri:str=Field(max_length=2048)
    allow_provisioning:bool=False


@router.get("/integrations/lti")
def platforms(db:Session=Depends(get_db),user=Depends(integration_owner)):
    return {"items":[{key:getattr(row,key) for key in ("id","name","issuer","client_id","deployment_id","authorization_endpoint","token_endpoint","jwks_uri","enabled","allow_provisioning")} for row in db.query(LTIPlatform).filter_by(organization_id=user.organization_id)]}


@router.post("/integrations/lti",status_code=201)
def create_platform(payload:PlatformInput,db:Session=Depends(get_db),user=Depends(integration_owner)):
    try:
        for url in (payload.issuer,payload.authorization_endpoint,payload.token_endpoint,payload.jwks_uri):validate_endpoint(url)
    except (ValueError,OSError):raise HTTPException(422,"Проверьте HTTPS-адреса LMS")
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    try:encrypted=seal(pem)
    except RuntimeError:raise HTTPException(503,"Оператор ещё не настроил шифрование интеграций")
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))|{"kid":str(uuid.uuid4()),"use":"sig","alg":"RS256"}
    row=LTIPlatform(organization_id=user.organization_id,created_by=user.id,private_key_encrypted=encrypted,public_jwk=public,**payload.model_dump())
    db.add(row);db.flush();audit(db,user,"lti.created","lti_platform",row.id,organization_id=user.organization_id);db.commit()
    root=settings.PUBLIC_APP_URL.rstrip("/")+f"/api/lti/{row.id}"
    return {"id":row.id,"login_url":root+"/login","launch_url":root+"/launch","jwks_url":root+"/jwks"}


@router.delete("/integrations/lti/{platform_id}")
def disable(platform_id:int,db:Session=Depends(get_db),user=Depends(integration_owner)):
    row=db.query(LTIPlatform).filter_by(id=platform_id,organization_id=user.organization_id).first()
    if not row:raise HTTPException(404,"LMS не найдена")
    row.enabled=False;audit(db,user,"lti.disabled","lti_platform",row.id,organization_id=user.organization_id);db.commit();return {"ok":True}


class ResourceInput(BaseModel):
    resource_link_id:str=Field(min_length=1,max_length=255)
    course_version_id:int=Field(gt=0)


@router.post("/integrations/lti/{platform_id}/resources",status_code=201)
def set_resource(platform_id:int,payload:ResourceInput,db:Session=Depends(get_db),user=Depends(integration_owner)):
    platform=db.query(LTIPlatform).filter_by(id=platform_id,organization_id=user.organization_id).first()
    if not platform:raise HTTPException(404,"LMS не найдена")
    from app.models.course_version import CourseVersion
    from app.models.course import Course
    version=db.get(CourseVersion,payload.course_version_id)
    course=db.get(Course,version.course_id) if version else None
    if not course or getattr(course,"organization_id",None)!=user.organization_id or version.publication_status!="published":raise HTTPException(404,"Опубликованная версия курса не найдена")
    row=db.query(LTIResource).filter_by(platform_id=platform_id,resource_link_id=payload.resource_link_id).first()
    if row and row.course_version_id!=payload.course_version_id:raise HTTPException(409,"Ссылка закреплена за версией. Для новой версии создайте новую ссылку LMS")
    if not row:row=LTIResource(platform_id=platform_id,**payload.model_dump());db.add(row)
    db.flush();audit(db,user,"lti.resource_mapped","lti_resource",row.id,organization_id=user.organization_id);db.commit();return {"id":row.id}


class IdentityInput(BaseModel):
    subject:str=Field(min_length=1,max_length=255)
    user_id:int=Field(gt=0)


@router.post("/integrations/lti/{platform_id}/identities",status_code=201)
def map_identity(platform_id:int,payload:IdentityInput,db:Session=Depends(get_db),user=Depends(integration_owner)):
    platform=db.query(LTIPlatform).filter_by(id=platform_id,organization_id=user.organization_id).first()
    member=db.query(User).filter_by(id=payload.user_id,organization_id=user.organization_id,is_active=True).first()
    if not platform or not member:raise HTTPException(404,"LMS или участник не найдены")
    row=db.query(LTIIdentity).filter_by(platform_id=platform_id,subject=payload.subject).first()
    if row and row.user_id!=member.id:raise HTTPException(409,"Учётная запись LMS уже связана с другим участником")
    if not row:row=LTIIdentity(platform_id=platform_id,subject=payload.subject,user_id=member.id);db.add(row)
    db.flush();audit(db,user,"lti.identity_mapped","user",member.id,platform_id=platform_id,organization_id=user.organization_id);db.commit();return {"id":row.id}


@router.get("/lti/{platform_id}/jwks")
def jwks(platform_id:int,db:Session=Depends(get_db)):
    platform=db.query(LTIPlatform).filter_by(id=platform_id,enabled=True).first()
    if not platform:raise HTTPException(404,"LMS не найдена")
    return {"keys":[platform.public_jwk]}


@router.get("/lti/{platform_id}/login")
def login(platform_id:int,iss:str=Query(max_length=2048),login_hint:str=Query(min_length=1,max_length=2048),
          target_link_uri:str=Query(max_length=2048),client_id:str|None=Query(None,max_length=255),lti_message_hint:str=Query("",max_length=4096),db:Session=Depends(get_db)):
    platform=db.query(LTIPlatform).filter_by(id=platform_id,enabled=True).first()
    if not platform or iss!=platform.issuer or (client_id and client_id!=platform.client_id) or target_link_uri!=lti_service.launch_url(platform):raise HTTPException(403,"LMS или адрес запуска не совпадает с настройкой")
    url,state,browser=lti_service.begin(db,platform,login_hint,lti_message_hint)
    response=RedirectResponse(url,status_code=303)
    response.set_cookie("lernium_lti_"+state[:12],browser,max_age=600,httponly=True,secure=True,samesite="none",path="/api/lti")
    return response


@router.post("/lti/{platform_id}/launch")
def launch(platform_id:int,request:Request,state:str=Form(min_length=32,max_length=128),id_token:str=Form(min_length=32,max_length=32768),db:Session=Depends(get_db)):
    platform=db.query(LTIPlatform).filter_by(id=platform_id,enabled=True).first()
    try:
        if not platform:raise HTTPException(403,"LMS отключена")
        code=lti_service.complete(db,platform,state,id_token,request.cookies.get("lernium_lti_"+state[:12]))
        fragment={"lti_code":code,"lti_state":state}
    except HTTPException as exc:
        db.rollback();fragment={"sso_error":str(exc.detail)}
    return RedirectResponse(settings.PUBLIC_APP_URL.rstrip("/")+"/auth#"+urlencode(fragment),status_code=303)


class ExchangeInput(BaseModel):
    code:str=Field(min_length=32,max_length=128)
    state:str=Field(min_length=32,max_length=128)


@router.post("/lti/exchange")
def exchange(payload:ExchangeInput,request:Request,db:Session=Depends(get_db)):
    row=db.get(LTISession,digest(payload.state));browser=request.cookies.get("lernium_lti_"+payload.state[:12])
    if not row or row.expires_at<=datetime.utcnow() or row.exchanged_at or row.exchange_code_hash!=digest(payload.code) or not browser or not secrets.compare_digest(row.browser_hash,digest(browser)):
        raise HTTPException(400,"Сеанс LMS недействителен. Откройте курс из LMS заново")
    platform=db.get(LTIPlatform,row.platform_id);user=db.get(User,row.authenticated_user_id)
    if not platform.enabled or not user or not user.is_active or user.organization_id!=platform.organization_id:raise HTTPException(403,"Доступ отозван")
    ensure_organization_active(user)
    claimed=db.execute(update(LTISession).where(LTISession.state_hash==row.state_hash,LTISession.exchanged_at.is_(None)).values(exchanged_at=datetime.utcnow()))
    if claimed.rowcount!=1:raise HTTPException(400,"Сеанс LMS уже использован")
    db.commit()
    response=JSONResponse(jsonable_encoder({"access_token":create_access_token(user.id,user.auth_version),"token_type":"bearer","assignment_id":row.assignment_id,"user":UserResponse.model_validate(user)}))
    response.delete_cookie("lernium_lti_"+payload.state[:12],path="/api/lti")
    return response
