"""LTI 1.3 launch validation and durable Assignment and Grade Services delivery."""
import base64
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit
import jwt
from fastapi import HTTPException
from sqlalchemy import update
from app.core.config import settings
from app.core.security import hash_password
from app.models.integrations import LTIPlatform, LTIResource, LTIIdentity, LTISession, LTIGradeBinding, OutboundMessage
from app.models.user import User
from app.services import integration_http
from app.services.integration_secrets import seal, unseal
from app.services.oidc_service import digest, verify_id_token
from app.services.organization_access import audit, lock_org, check_seat

CLAIM="https://purl.imsglobal.org/spec/lti/claim/"
AGS_CLAIM="https://purl.imsglobal.org/spec/lti-ags/claim/endpoint"
SCORE_SCOPE="https://purl.imsglobal.org/spec/lti-ags/scope/score"


def launch_url(platform):return settings.PUBLIC_APP_URL.rstrip("/")+f"/api/lti/{platform.id}/launch"


def begin(db,platform,login_hint,message_hint=""):
    state,nonce,browser=(secrets.token_urlsafe(32) for _ in range(3))
    db.add(LTISession(state_hash=digest(state),platform_id=platform.id,nonce_hash=digest(nonce),browser_hash=digest(browser),expires_at=datetime.utcnow()+timedelta(minutes=10)))
    db.commit()
    params={"scope":"openid","response_type":"id_token","response_mode":"form_post","prompt":"none",
        "client_id":platform.client_id,"redirect_uri":launch_url(platform),"login_hint":login_hint,"state":state,"nonce":nonce}
    if message_hint:params["lti_message_hint"]=message_hint
    return platform.authorization_endpoint+("&" if "?" in platform.authorization_endpoint else "?")+urlencode(params),state,browser


def resolve_user(db,platform,claims):
    identity=db.query(LTIIdentity).filter_by(platform_id=platform.id,subject=claims["sub"]).first()
    if identity:
        user=db.get(User,identity.user_id)
        if not user or not user.is_active or user.organization_id!=platform.organization_id:raise HTTPException(403,"Доступ участника отозван")
        return user
    if not platform.allow_provisioning:raise HTTPException(403,"Администратор должен связать участника LMS с аккаунтом Лерниума")
    org=lock_org(db,platform.organization_id);check_seat(db,org)
    # LTI email is not an email-verification assertion: never merge by it or send password resets to it.
    user=User(email=f"{uuid.uuid4().hex}@lti.invalid",password_hash=hash_password(secrets.token_urlsafe(48)),
        full_name=str(claims.get("name", "Участник LMS"))[:160],organization_id=org.id,organization_role="member")
    db.add(user);db.flush();db.add(LTIIdentity(platform_id=platform.id,subject=claims["sub"],user_id=user.id))
    return user


def complete(db,platform,raw_state,id_token,browser):
    row=db.get(LTISession,digest(raw_state))
    now=datetime.utcnow()
    if not row or row.platform_id!=platform.id or row.consumed_at or row.expires_at<=now or not browser or not secrets.compare_digest(row.browser_hash,digest(browser)):
        raise HTTPException(400,"Сеанс LMS истёк или повторён. Откройте курс из LMS заново")
    claims=verify_id_token(id_token,platform,row.nonce_hash)
    if claims.get(CLAIM+"version")!="1.3.0" or claims.get(CLAIM+"message_type")!="LtiResourceLinkRequest" or claims.get(CLAIM+"deployment_id")!=platform.deployment_id:
        raise HTTPException(403,"Недопустимое LTI-сообщение или развёртывание LMS")
    if claims.get(CLAIM+"target_link_uri")!=launch_url(platform):raise HTTPException(403,"Неверный адрес назначения LTI")
    resource_id=claims.get(CLAIM+"resource_link",{}).get("id")
    resource=db.query(LTIResource).filter_by(platform_id=platform.id,resource_link_id=resource_id).first()
    if not resource:raise HTTPException(404,"Курс для этой ссылки LMS не настроен")
    org=lock_org(db,platform.organization_id)
    if not org.is_active:raise HTTPException(403,"Организация приостановлена")
    claimed=db.execute(update(LTISession).where(LTISession.state_hash==row.state_hash,LTISession.consumed_at.is_(None)).values(consumed_at=now))
    if claimed.rowcount!=1:raise HTTPException(400,"Этот запуск LMS уже обработан")
    user=resolve_user(db,platform,claims)
    from app.services.learning_service import assign_from_integration
    assignment,created=assign_from_integration(db,org_id=org.id,version_id=resource.course_version_id,user_id=user.id,
        actor_id=platform.created_by,cycle_key=f"lti:{platform.id}:{resource.id}")
    ags=claims.get(AGS_CLAIM,{})
    lineitem=ags.get("lineitem")
    if lineitem and SCORE_SCOPE in ags.get("scope",[]):
        target=urlsplit(lineitem);issuer=urlsplit(platform.issuer)
        if (target.scheme,target.netloc)!=(issuer.scheme,issuer.netloc):raise HTTPException(403,"Адрес оценок LMS не принадлежит настроенному сервису")
        try:integration_http.validate_endpoint(lineitem)
        except (ValueError,OSError):raise HTTPException(403,"Недопустимый адрес оценок LMS")
        binding=db.query(LTIGradeBinding).filter_by(platform_id=platform.id,assignment_id=assignment.id,subject=claims["sub"]).first()
        if not binding:db.add(LTIGradeBinding(platform_id=platform.id,assignment_id=assignment.id,subject=claims["sub"],lineitem_url=lineitem))
        elif binding.lineitem_url!=lineitem:raise HTTPException(409,"LMS изменила журнал оценок. Администратор должен подтвердить новое подключение")
    exchange=secrets.token_urlsafe(32)
    row.exchange_code_hash=digest(exchange);row.authenticated_user_id=user.id;row.assignment_id=assignment.id
    row.expires_at=datetime.utcnow()+timedelta(minutes=2)
    audit(db,user,"lti.launched","assignment",assignment.id,platform_id=platform.id,organization_id=org.id)
    db.commit();return exchange


def queue_grade(db,assignment_id,score_given,score_maximum,updated_at):
    if not 0<=score_given<=score_maximum or score_maximum<=0:raise ValueError("Invalid grade scale")
    instant=updated_at.astimezone(timezone.utc).replace(tzinfo=None) if updated_at.tzinfo else updated_at
    for binding in db.query(LTIGradeBinding).filter_by(assignment_id=assignment_id).with_for_update():
        if binding.last_queued_at and instant<=binding.last_queued_at:continue
        platform=db.get(LTIPlatform,binding.platform_id)
        if not platform.enabled:continue
        binding.last_queued_at=instant
        payload={"binding_id":binding.id,"timestamp":instant.replace(tzinfo=timezone.utc).isoformat(),
            "scoreGiven":score_given,"scoreMaximum":score_maximum,"activityProgress":"Completed","gradingProgress":"FullyGraded","userId":binding.subject}
        key=f"lti-score:{binding.id}:{instant.isoformat()}"
        db.add(OutboundMessage(id=str(uuid.uuid4()),kind="lti_score",organization_id=platform.organization_id,
            deduplication_key=key,payload_encrypted=seal(payload),available_at=datetime.utcnow()))


def deliver_score(db,payload):
    binding=db.get(LTIGradeBinding,payload["binding_id"])
    if not binding:raise ValueError("Missing LTI grade binding")
    instant=datetime.fromisoformat(payload["timestamp"]).replace(tzinfo=None)
    if binding.last_queued_at and instant<binding.last_queued_at:return
    if binding.last_delivered_at and instant<=binding.last_delivered_at:return
    platform=db.get(LTIPlatform,binding.platform_id)
    if not platform.enabled:raise ValueError("LTI platform disabled")
    now=datetime.now(timezone.utc)
    assertion=jwt.encode({"iss":platform.client_id,"sub":platform.client_id,"aud":platform.token_endpoint,
        "iat":now,"exp":now+timedelta(minutes=5),"jti":str(uuid.uuid4())},unseal(platform.private_key_encrypted),algorithm="RS256",headers={"kid":platform.public_jwk["kid"]})
    body=urlencode({"grant_type":"client_credentials","client_assertion_type":"urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion":assertion,"scope":SCORE_SCOPE}).encode()
    token=integration_http.request_json("POST",platform.token_endpoint,body,{"Content-Type":"application/x-www-form-urlencoded"})
    access=token.get("access_token")
    if not isinstance(access,str) or not access or token.get("token_type","").lower()!="bearer":raise ValueError("Invalid LMS service token")
    score={k:v for k,v in payload.items() if k!="binding_id"}
    integration_http.request("POST",binding.lineitem_url.rstrip("/")+"/scores",json.dumps(score).encode(),
        {"Content-Type":"application/vnd.ims.lis.v1.score+json","Authorization":"Bearer "+access})
    binding.last_delivered_at=instant
