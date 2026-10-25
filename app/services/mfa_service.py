"""RFC 6238 TOTP, account-bound challenge and single-use recovery-code state.

Secrets are encrypted at rest with the separately configured data key. Successful
factor consumption and JWT issuance are one transaction. Invalid guesses commit
their counters explicitly; request rollback cannot reset the attempt budget.
"""
from app.core.surface import surface
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from app.core.mfa_config import mfa_settings
from app.core.security import create_access_token, verify_password
from app.models.mfa import UserMFA, MFARecoveryCode, MFAChallenge
from app.models.user import User
from app.schemas.auth import UserResponse
from app.services.integration_secrets import seal, unseal, cipher
from app.services.organization_access import audit


def now(): return datetime.fromtimestamp(time.time(), timezone.utc).replace(tzinfo=None)
def digest(raw): return hashlib.sha256(raw.encode()).hexdigest()


def otp(secret, step):
    key = base64.b32decode(secret + '='*((8-len(secret)%8)%8))
    hashed = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = hashed[-1] & 15
    return str((struct.unpack('>I', hashed[offset:offset+4])[0] & 0x7fffffff) % 1_000_000).zfill(6)


def matching_step(secret, code):
    if not re.fullmatch(r'\d{6}', code): return None
    current = int(time.time()//30)
    matches = [step for step in range(max(0,current-1),current+2) if hmac.compare_digest(otp(secret,step),code)]
    return max(matches) if matches else None


def locked_factor(db, user_id):
    # Serialize factor changes even before the optional row exists.
    db.execute(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    factor = db.execute(select(UserMFA).where(UserMFA.user_id==user_id).execution_options(populate_existing=True)).scalar_one_or_none()
    if not factor:
        factor = UserMFA(user_id=user_id)
        db.add(factor); db.flush()
    if factor.locked_until and factor.locked_until > now():
        raise HTTPException(429, 'Слишком много неверных кодов. Повторите через 15 минут.', headers={'Retry-After':'900'})
    if factor.locked_until:
        factor.locked_until = None; factor.failed_attempts = 0
    return factor


def failure(db, user, factor, challenge=None):
    factor.failed_attempts += 1
    if factor.failed_attempts >= 10:
        factor.locked_until = now()+timedelta(minutes=15)
    if challenge:
        challenge.attempts += 1
        if challenge.attempts >= 5: challenge.consumed_at = now()
    audit(db,user,'mfa.verification_failed','user',user.id)
    db.commit()
    raise HTTPException(400, 'Код неверен, уже использован или истёк. Введите новый код либо код восстановления.')


def require_password(user, password):
    if not verify_password(password, user.password_hash):
        raise HTTPException(400, 'Текущий пароль неверен')


def authenticated_factor(db,user,claims):
    """Recheck revocation under the same lock used to issue a replacement JWT."""
    factor=locked_factor(db,user.id)
    if not user.is_active or user.auth_version!=claims.get('ver',0):
        raise HTTPException(401,'Сеанс завершён. Войдите снова.',headers={'WWW-Authenticate':'Bearer'})
    from app.services.auth_service import ensure_organization_active
    db.expire(user,['organization'])
    ensure_organization_active(user)
    return factor


def enabled(db, user):
    factor = db.get(UserMFA, user.id)
    return bool(factor and factor.enabled)


def secret_for(factor, *, pending=False):
    value = unseal(factor.pending_secret_encrypted if pending else factor.secret_encrypted)
    if not isinstance(value,dict) or value.get('purpose') != 'totp' or value.get('user_id') != factor.user_id:
        raise RuntimeError('Invalid encrypted factor binding')
    return value['secret']


def consume(db, user, factor, code, challenge=None):
    if not factor.enabled:
        raise HTTPException(409, 'Двухэтапный вход не подключён')
    normalized = re.sub(r'[\s-]', '', code).lower()
    method = None
    if re.fullmatch(r'\d{6}', normalized):
        step = matching_step(secret_for(factor), normalized)
        if step is not None:
            claimed = db.execute(update(UserMFA).where(UserMFA.user_id==user.id, UserMFA.enabled.is_(True),
                UserMFA.last_totp_step < step).values(last_totp_step=step))
            if claimed.rowcount == 1: method = 'otp'
    elif re.fullmatch(r'[0-9a-f]{24}', normalized):
        claimed = db.execute(update(MFARecoveryCode).where(MFARecoveryCode.user_id==user.id,
            MFARecoveryCode.code_hash==digest(f'mfa-recovery:{user.id}:{normalized}'),
            MFARecoveryCode.consumed_at.is_(None)).values(consumed_at=now()))
        if claimed.rowcount == 1: method = 'recovery'
    if not method: failure(db,user,factor,challenge)
    factor.failed_attempts = 0; factor.locked_until = None
    if method == 'recovery': audit(db,user,'mfa.recovery_consumed','user',user.id)
    return method


def codes(db,user):
    db.execute(delete(MFARecoveryCode).where(MFARecoveryCode.user_id==user.id))
    result=[]
    for _ in range(10):
        raw=secrets.token_hex(12)
        db.add(MFARecoveryCode(user_id=user.id,code_hash=digest(f'mfa-recovery:{user.id}:{raw}')))
        result.append('-'.join(raw[index:index+4] for index in range(0,len(raw),4)))
    return result


def token_response(user, *, primary='pwd', factor=None, extra=None):
    return {'access_token':create_access_token(user.id,user.auth_version,
        amr=[primary,factor] if factor else [primary], mfa_at=int(time.time()) if factor else None),
        'token_type':'bearer','user':UserResponse.model_validate(user), **(extra or {})}


def authentication_result(db, user, *, primary='pwd', context=None):
    if not enabled(db,user): return token_response(user,primary=primary,extra={'assignment_id':context['assignment_id']} if context and context.get('assignment_id') else None)
    factor=locked_factor(db,user.id)
    # Old unfinished logins are invalidated; a challenge is not an access token.
    db.execute(update(MFAChallenge).where(MFAChallenge.user_id==user.id,MFAChallenge.consumed_at.is_(None)).values(consumed_at=now()))
    raw=secrets.token_urlsafe(32)
    expires=now()+timedelta(minutes=5)
    db.add(MFAChallenge(token_hash=digest(raw),user_id=user.id,auth_version=user.auth_version,
                        primary_method=primary,context={**(context or {}), "surface": surface.get()},expires_at=expires))
    audit(db,user,'mfa.challenge_created','user',user.id,primary_method=primary)
    db.flush()
    return {'mfa_required':True,'challenge_token':raw,'expires_at':expires.isoformat()+'Z'}


def validate_federation(db,user,context):
    from app.services.auth_service import ensure_organization_active
    ensure_organization_active(user)
    if not context or not (context.get("oidc_provider_id") or context.get("lti_platform_id")): return
    from app.services.integration_access import ensure_integrations_enabled
    if context.get('organization_id') != user.organization_id:
        raise HTTPException(403,'Доступ организации изменён. Повторите вход.')
    ensure_integrations_enabled(db,user.organization_id)
    if context.get('oidc_provider_id'):
        from app.models.integrations import OIDCProvider
        provider=db.get(OIDCProvider,context['oidc_provider_id'])
    else:
        from app.models.integrations import LTIPlatform
        provider=db.get(LTIPlatform,context.get('lti_platform_id'))
    if not provider or not provider.enabled or provider.organization_id!=user.organization_id:
        raise HTTPException(403,'Подключение отозвано. Повторите вход другим способом.')


def complete_challenge(db,raw,code):
    challenge=db.get(MFAChallenge,digest(raw))
    if not challenge or challenge.consumed_at or challenge.expires_at<=now() or challenge.attempts>=5:
        raise HTTPException(400,'Проверка входа истекла или уже использована. Начните вход заново.')
    if challenge.context.get("surface", "platform") != surface.get():
        raise HTTPException(400, "Проверка входа относится к другому сервису")
    user=db.get(User,challenge.user_id)
    if surface.get() == "admin" and user and not user.is_admin:
        raise HTTPException(403, "Доступ оператора отозван")
    if not user or not user.is_active or user.auth_version!=challenge.auth_version:
        raise HTTPException(400,'Доступ изменён. Начните вход заново.')
    factor=locked_factor(db,user.id)
    db.refresh(challenge)
    if not user.is_active or user.auth_version!=challenge.auth_version or challenge.consumed_at or challenge.expires_at<=now():
        raise HTTPException(400,'Проверка входа истекла или уже использована. Начните вход заново.')
    validate_federation(db,user,challenge.context)
    method=consume(db,user,factor,code,challenge)
    claimed=db.execute(update(MFAChallenge).where(MFAChallenge.token_hash==challenge.token_hash,
        MFAChallenge.consumed_at.is_(None),MFAChallenge.expires_at>now()).values(consumed_at=now()))
    if claimed.rowcount!=1:
        raise HTTPException(400,'Проверка входа уже использована')
    audit(db,user,'mfa.login_verified','user',user.id,method=method)
    if challenge.context.get('mobile'):
        from app.services.mobile_sessions import issue
        result=issue(db,user,amr=[challenge.primary_method,method],mfa_at=int(time.time()),
                     device_name=challenge.context.get('device_name','Mobile device'))
    else:
        result=token_response(user,primary=challenge.primary_method,factor=method,
            extra={'assignment_id':challenge.context['assignment_id']} if challenge.context.get('assignment_id') else None)
    db.commit()
    return result


def start_enrollment(db,user,password,claims):
    factor=authenticated_factor(db,user,claims)
    require_password(user,password)
    if factor.enabled: raise HTTPException(409,'Двухэтапный вход уже подключён')
    try: cipher()  # Fail before returning a secret if durable encryption is unavailable.
    except (ValueError,RuntimeError):
        raise HTTPException(503,'Защита кодов пока не настроена оператором. Подключение двухэтапного входа недоступно.')
    raw=secrets.token_urlsafe(32)
    secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    factor.pending_secret_encrypted=seal({'purpose':'totp','user_id':user.id,'secret':secret})
    factor.enrollment_token_hash=digest(raw)
    factor.enrollment_expires_at=now()+timedelta(minutes=10)
    audit(db,user,'mfa.enrollment_started','user',user.id)
    db.commit()
    uri='otpauth://totp/'+quote('Lernium:'+(user.email or user.username or str(user.id)),safe='')+'?'+urlencode({'secret':secret,'issuer':'Lernium','algorithm':'SHA1','digits':6,'period':30})
    return {'enrollment_token':raw,'secret':secret,'otpauth_uri':uri,'expires_at':factor.enrollment_expires_at.isoformat()+'Z'}


def confirm_enrollment(db,user,raw,code,claims):
    factor=authenticated_factor(db,user,claims)
    if factor.enabled or not factor.enrollment_token_hash or not hmac.compare_digest(factor.enrollment_token_hash,digest(raw)) or not factor.enrollment_expires_at or factor.enrollment_expires_at<=now():
        raise HTTPException(400,'Настройка истекла или уже завершена. Начните заново.')
    step=matching_step(secret_for(factor,pending=True),code)
    if step is None: failure(db,user,factor)
    factor.secret_encrypted=factor.pending_secret_encrypted
    factor.pending_secret_encrypted='';factor.enrollment_token_hash=None;factor.enrollment_expires_at=None
    factor.enabled=True;factor.enabled_at=now();factor.last_totp_step=step
    factor.failed_attempts=0;factor.locked_until=None
    user.auth_version+=1
    recovery=codes(db,user)
    audit(db,user,'mfa.enabled','user',user.id)
    result=token_response(user,factor='otp',extra={'recovery_codes':recovery})
    db.commit()
    return result


def require_operator_factor(db,user,claims):
    factor=db.get(UserMFA,user.id)
    if not factor or not factor.enabled:
        if mfa_settings.MFA_REQUIRED_FOR_OPERATORS:
            raise HTTPException(403,{'code':'mfa_enrollment_required','message':'Для действий оператора подключите двухэтапный вход в безопасности профиля.'})
        return
    timestamp=claims.get('mfa_at')
    if not isinstance(timestamp,int) or not 0<=time.time()-timestamp<=mfa_settings.MFA_STEP_UP_SECONDS or not set(claims.get('amr',[])) & {'otp','recovery'}:
        raise HTTPException(403,{'code':'mfa_step_up_required','message':'Подтвердите двухэтапный вход и повторите действие. Введённые данные сохранены.'})


def preserve_token(user,claims):
    """Account changes revoke other sessions without upgrading an old MFA timestamp."""
    return create_access_token(user.id,user.auth_version,amr=claims.get('amr',[]),mfa_at=claims.get('mfa_at'))
