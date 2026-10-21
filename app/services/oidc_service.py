import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode
import jwt
from fastapi import HTTPException
from sqlalchemy import update
from app.core.config import settings
from app.core.security import hash_password, normalize_email
from app.models.integrations import OIDCProvider, OIDCSession, ExternalIdentity
from app.models.user import User
from app.services.auth_service import ensure_organization_active
from app.services import integration_http
from app.services.integration_secrets import seal, unseal
from app.services.organization_access import audit, check_seat, lock_org


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def callback_url():return settings.PUBLIC_APP_URL.rstrip("/")+"/api/auth/oidc/callback"


def begin(db,provider,link_user_id=None):
    from app.services.integration_access import ensure_integrations_enabled
    ensure_integrations_enabled(db, provider.organization_id)
    raw_state,nonce,browser,verifier=(secrets.token_urlsafe(32) for _ in range(4))
    row=OIDCSession(state_hash=digest(raw_state),provider_id=provider.id,nonce_hash=digest(nonce),browser_hash=digest(browser),
        verifier_encrypted=seal(verifier),link_user_id=link_user_id,expires_at=datetime.utcnow()+timedelta(minutes=10))
    db.add(row);db.commit()
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params={"response_type":"code","client_id":provider.client_id,"redirect_uri":callback_url(),"scope":"openid email profile",
        "state":raw_state,"nonce":nonce,"code_challenge":challenge,"code_challenge_method":"S256"}
    return provider.authorization_endpoint+("&" if "?" in provider.authorization_endpoint else "?")+urlencode(params),raw_state,browser


def verify_id_token(encoded,provider,nonce_hash):
    try:jwks=integration_http.request_json("GET",provider.jwks_uri)
    except Exception:raise HTTPException(503,"Не удалось проверить ключи сервиса входа. Повторите вход позже")
    try:
        header=jwt.get_unverified_header(encoded)
        if header.get("alg") not in {"RS256","ES256"} or not isinstance(header.get("kid"),str):raise ValueError()
        keys=[key for key in jwks.get("keys",[]) if key.get("kid")==header["kid"] and key.get("use","sig")=="sig"]
        if len(keys)!=1 or len(jwks.get("keys",[]))>100:raise ValueError()
        public=jwt.PyJWK.from_dict(keys[0],algorithm=header["alg"]).key
        claims=jwt.decode(encoded,public,algorithms=[header["alg"]],issuer=provider.issuer,audience=provider.client_id,
            leeway=30,options={"require":["iss","sub","aud","exp","iat","nonce"]})
        audiences=claims["aud"] if isinstance(claims["aud"],list) else [claims["aud"]]
        if (len(audiences)>1 and claims.get("azp")!=provider.client_id) or (claims.get("azp") and claims["azp"]!=provider.client_id):raise ValueError()
        if not isinstance(claims["sub"],str) or not 1<=len(claims["sub"])<=255:raise ValueError()
        if not isinstance(claims["nonce"],str) or not secrets.compare_digest(digest(claims["nonce"]),nonce_hash):raise ValueError()
        return claims
    except (jwt.PyJWTError,ValueError,TypeError,KeyError):
        raise HTTPException(401,"Сервис входа вернул неподтверждённые данные")


def resolve_identity(db,provider,claims,link_user_id=None):
    from app.services.integration_access import ensure_integrations_enabled
    ensure_integrations_enabled(db, provider.organization_id)
    org=lock_org(db,provider.organization_id)
    if not org.is_active:raise HTTPException(403,"Доступ организации приостановлен")
    identity=db.query(ExternalIdentity).filter_by(provider_id=provider.id,subject=claims["sub"]).first()
    if identity:
        if link_user_id and identity.user_id!=link_user_id:raise HTTPException(409,"Эта внешняя учётная запись уже связана")
        user=db.get(User,identity.user_id)
        if not user or not user.is_active or user.organization_id!=org.id:raise HTTPException(403,"Доступ этой учётной записи отозван")
        return user
    if link_user_id:
        user=db.get(User,link_user_id)
        if not user or not user.is_active or user.organization_id!=org.id:raise HTTPException(403,"Нельзя связать учётную запись")
    else:
        if not provider.allow_provisioning:raise HTTPException(403,"Сначала войдите обычным способом и свяжите внешний аккаунт в профиле")
        email=claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email,str) or "@" not in email:
            raise HTTPException(403,"Сервис входа не подтвердил адрес почты")
        email=normalize_email(email)
        if email.rsplit("@",1)[1] not in provider.allowed_domains:raise HTTPException(403,"Домен почты не разрешён для этой организации")
        # Even a signed, verified email is not proof of ownership of an existing local account.
        if db.query(User.id).filter_by(email=email).first():raise HTTPException(409,"Аккаунт уже существует. Войдите паролем и явно свяжите внешний аккаунт в профиле")
        check_seat(db,org)
        user=User(email=email,password_hash=hash_password(secrets.token_urlsafe(48)),full_name=str(claims.get("name",""))[:160],
            organization_id=org.id,organization_role="member")
        db.add(user);db.flush()
    db.add(ExternalIdentity(provider_id=provider.id,subject=claims["sub"],user_id=user.id))
    audit(db,user,"identity.linked","user",user.id,provider_id=provider.id,organization_id=org.id)
    return user


def complete(db,raw_state,code,browser):
    row=db.get(OIDCSession,digest(raw_state))
    now=datetime.utcnow()
    if not row or row.consumed_at or row.expires_at<=now or not browser or not secrets.compare_digest(row.browser_hash,digest(browser)):
        raise HTTPException(400,"Сеанс внешнего входа истёк или уже использован. Начните вход заново")
    provider=db.get(OIDCProvider,row.provider_id)
    if not provider or not provider.enabled:raise HTTPException(403,"Внешний вход отключён")
    claimed=db.execute(update(OIDCSession).where(OIDCSession.state_hash==row.state_hash,OIDCSession.consumed_at.is_(None)).values(consumed_at=now))
    if claimed.rowcount!=1:raise HTTPException(400,"Этот сеанс входа уже использован")
    db.commit()
    body=urlencode({"grant_type":"authorization_code","code":code,"redirect_uri":callback_url(),"client_id":provider.client_id,
        "client_secret":unseal(provider.client_secret_encrypted),"code_verifier":unseal(row.verifier_encrypted)}).encode()
    try:token_response=integration_http.request_json("POST",provider.token_endpoint,body,{"Content-Type":"application/x-www-form-urlencoded"})
    except Exception:raise HTTPException(503,"Сервис входа временно недоступен. Повторите вход")
    claims=verify_id_token(token_response.get("id_token",""),provider,row.nonce_hash)
    user=resolve_identity(db,provider,claims,row.link_user_id)
    ensure_organization_active(user)
    raw_exchange=secrets.token_urlsafe(32)
    row.exchange_code_hash=digest(raw_exchange);row.authenticated_user_id=user.id
    row.expires_at=datetime.utcnow()+timedelta(minutes=2);row.verifier_encrypted=""
    audit(db,user,"identity.login","user",user.id,provider_id=provider.id,organization_id=provider.organization_id)
    db.commit()
    return raw_exchange
