from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.core.mfa_config import mfa_settings
from app.database.db import get_db
from app.models.mfa import UserMFA, MFARecoveryCode
from app.services.auth_service import get_current_user
from app.services import mfa_service as svc
from app.services.organization_access import audit

router=APIRouter()


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class CodeInput(Input):
    code: str = Field(min_length=6,max_length=64)


class PasswordInput(Input):
    # Do not trim passwords: spaces may be meaningful.
    model_config=ConfigDict(extra='forbid')
    password: str = Field(min_length=1,max_length=128)


class ManageInput(PasswordInput):
    code: str = Field(min_length=6,max_length=64)


class EnrollmentInput(CodeInput):
    enrollment_token: str = Field(min_length=32,max_length=128)


class ChallengeInput(CodeInput):
    challenge_token: str = Field(min_length=32,max_length=128)


@router.get('/auth/mfa/status')
def status(db:Session=Depends(get_db),user=Depends(get_current_user)):
    factor=db.get(UserMFA,user.id)
    try:svc.cipher(); available=True
    except (ValueError,RuntimeError):available=False
    return {'enabled':bool(factor and factor.enabled),'enrollment_available':available,
        'required_for_operator':bool(user.is_admin and mfa_settings.MFA_REQUIRED_FOR_OPERATORS),
        'remaining_recovery_codes':db.query(MFARecoveryCode).filter_by(user_id=user.id,consumed_at=None).count(),
        'enabled_at':factor.enabled_at if factor else None}


@router.post('/auth/mfa/enroll')
def enroll(payload:PasswordInput,request:Request,db:Session=Depends(get_db),user=Depends(get_current_user)):
    return svc.start_enrollment(db,user,payload.password,request.state.auth_claims)


@router.post('/auth/mfa/enroll/confirm')
def confirm(payload:EnrollmentInput,request:Request,db:Session=Depends(get_db),user=Depends(get_current_user)):
    return svc.confirm_enrollment(db,user,payload.enrollment_token,payload.code,request.state.auth_claims)


@router.post('/auth/mfa/verify')
def verify(payload:ChallengeInput,db:Session=Depends(get_db)):
    return svc.complete_challenge(db,payload.challenge_token,payload.code)


@router.post('/auth/mfa/step-up')
def step_up(payload:CodeInput,request:Request,db:Session=Depends(get_db),user=Depends(get_current_user)):
    factor=svc.authenticated_factor(db,user,request.state.auth_claims)
    method=svc.consume(db,user,factor,payload.code)
    primary=next((x for x in request.state.auth_claims.get('amr',[]) if x in {'pwd','oidc','lti'}),'pwd')
    audit(db,user,'mfa.step_up','user',user.id,method=method)
    result=svc.token_response(user,primary=primary,factor=method)
    db.commit()
    return result


@router.post('/auth/mfa/recovery-codes')
def replace_codes(payload:ManageInput,request:Request,db:Session=Depends(get_db),user=Depends(get_current_user)):
    factor=svc.authenticated_factor(db,user,request.state.auth_claims)
    svc.require_password(user,payload.password)
    method=svc.consume(db,user,factor,payload.code)
    recovery=svc.codes(db,user)
    audit(db,user,'mfa.recovery_replaced','user',user.id)
    result=svc.token_response(user,factor=method,extra={'recovery_codes':recovery})
    db.commit()
    return result


@router.post('/auth/mfa/disable')
def disable(payload:ManageInput,request:Request,db:Session=Depends(get_db),user=Depends(get_current_user)):
    factor=svc.authenticated_factor(db,user,request.state.auth_claims)
    if user.is_admin and mfa_settings.MFA_REQUIRED_FOR_OPERATORS:
        raise HTTPException(403,'Политика операторов требует двухэтапный вход. Отключение недоступно.')
    svc.require_password(user,payload.password)
    svc.consume(db,user,factor,payload.code)
    factor.enabled=False;factor.secret_encrypted='';factor.pending_secret_encrypted=''
    factor.enrollment_token_hash=None;factor.enrollment_expires_at=None;factor.enabled_at=None
    db.query(MFARecoveryCode).filter_by(user_id=user.id).delete()
    user.auth_version+=1
    audit(db,user,'mfa.disabled','user',user.id)
    result=svc.token_response(user)
    db.commit()
    return result
