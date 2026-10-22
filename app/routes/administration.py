from datetime import datetime, timedelta
import hashlib
import secrets
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import create_access_token, hash_password, normalize_email
from app.database.db import get_db
from app.models.administration import Organization, Invitation, AdminEvent
from app.models.account import PasswordReset
from app.models.ai_state import AICall
from app.models.course import Course
from app.models.document import Document
from app.models.course_graph import CourseGraph
from app.models.chat import Chat
from app.models.generation_run import GenerationRun
from app.models.legal import LegalRequest
from app.models.user import User
from app.schemas.auth import AuthCredentials, UserResponse, AuthResponse
from app.services.auth_service import get_current_user, require_admin
from app.services.legal_service import record_receipt, require_current_version
from app.services.organization_access import audit, check_course_limit, check_seat, lock_org, lock_member, lock_members, member_ids, seats

router = APIRouter()


class OrgInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=200)
    kind: Literal['business','education','government','individual'] = 'business'
    is_active: StrictBool = True
    seat_limit: int = Field(default=10, ge=1, le=100000)
    course_limit: int = Field(default=50, ge=1, le=1000000)
    source_limit: int = Field(default=20, ge=1, le=1000)
    generation_limit: int = Field(default=100, ge=0, le=1000000)


class InviteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=5, max_length=255)
    organization_id: int = Field(gt=0)
    role: Literal['owner','admin','author','reviewer','manager','member'] = 'member'
    locale: Literal['ru', 'en'] = 'ru'
    @field_validator('email')
    @classmethod
    def email_valid(cls, value):
        return AuthCredentials.valid_email(value)


class UserChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_active: StrictBool
    organization_id: int | None = Field(default=None, gt=0)
    organization_role: Literal['owner','admin','author','reviewer','manager','member'] = 'member'


class TokenInput(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class InviteAccept(TokenInput):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=160)
    terms_accepted: StrictBool
    marketing_accepted: StrictBool = False
    legal_version: str = Field(min_length=1, max_length=32)


def org_dict(db, org):
    users = db.query(User).filter(User.organization_id == org.id)
    return {**{key:getattr(org,key) for key in ['id','name','kind','is_active','seat_limit','course_limit','source_limit','generation_limit','created_at']},
            'active_users':users.filter(User.is_active.is_(True)).count(), 'reserved_seats':seats(db,org.id),
            'courses':db.query(Course).filter(Course.organization_id == org.id, Course.is_deleted.is_(False)).count()}


def user_dict(user):
    return {**UserResponse.model_validate(user).model_dump(), 'is_active':user.is_active,
            'created_at':user.created_at, 'organization_name':user.organization.name if user.organization else None}


def invitation_dict(row):
    status = 'accepted' if row.accepted_at else 'revoked' if row.revoked_at else 'expired' if row.expires_at <= datetime.utcnow() else 'pending'
    return {key:getattr(row,key) for key in ['id','email','organization_id','role','created_at','expires_at']} | {'status':status}


def scoped_team(user):
    if not user.organization_id:
        raise HTTPException(409, 'Аккаунт не подключён к организации')
    return user.organization_id


def require_team_owner(user, db):
    org_id = scoped_team(user)
    from app.services.learning_service import is_manager
    if not is_manager(db, user):
        raise HTTPException(403, 'Приглашать участников может администратор организации')
    return org_id


def make_invitation(db, actor, payload):
    actor = lock_member(db,actor.id,extra_org_ids=(payload.organization_id,),expected_auth_version=actor.auth_version)
    if not actor.is_admin and require_team_owner(actor,db) != payload.organization_id:
        raise HTTPException(403,'Недоступная организация')
    org = db.get(Organization, payload.organization_id)
    check_seat(db, org)
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing and (existing.is_admin or existing.organization_id):
        raise HTTPException(409, 'Аккаунт уже связан с организацией или является оператором. Сначала завершите текущие отношения доступа.')
    if not actor.is_admin:
        if payload.role == 'owner':
            raise HTTPException(403, 'Владение передаётся с отдельным подтверждением нового владельца')
        if payload.role == 'admin' and actor.organization_role != 'owner':
            raise HTTPException(403, 'Приглашать администратора может только владелец')
    if payload.role == 'owner' and db.query(User).filter_by(organization_id=org.id, organization_role='owner', is_active=True).count():
        raise HTTPException(409, 'У организации уже есть владелец. Используйте подтверждаемую передачу владения.')
    if db.query(Invitation).filter(Invitation.email == payload.email, Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None), Invitation.expires_at > datetime.utcnow()).first():
        raise HTTPException(409, 'Действующее приглашение уже существует. Сначала отзовите его.')
    raw = secrets.token_urlsafe(32)
    row = Invitation(email=payload.email, organization_id=org.id, role=payload.role,
        invited_by=actor.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=datetime.utcnow()+timedelta(days=7))
    db.add(row); db.flush()
    audit(db, actor, 'invitation.created', 'invitation', row.id, organization_id=org.id, role=payload.role)
    url = settings.PUBLIC_APP_URL.rstrip('/')+'/invite#token='+raw
    mail_status = 'not_configured'
    import importlib.util
    if importlib.util.find_spec('app.services.mail_service'):
        from app.services.mail_service import configured
        if configured():
            from app.services.mail_service import queue_invitation
            queue_invitation(db, payload.email, url, org.name, locale=payload.locale)
            mail_status = 'queued'
    db.commit()
    return {**invitation_dict(row), 'url':url, 'mail_status':mail_status, 'existing_account':bool(existing)}


@router.get('/admin/organizations')
def organizations(q: str = Query('', max_length=100), offset:int=Query(0,ge=0), limit:int=Query(50,ge=1,le=100),
                  db:Session=Depends(get_db), admin:User=Depends(require_admin)):
    query=db.query(Organization)
    if q:query=query.filter(Organization.name.icontains(q, autoescape=True))
    return {'total':query.count(),'items':[org_dict(db,o) for o in query.order_by(Organization.id.desc()).offset(offset).limit(limit)]}


@router.post('/admin/organizations', status_code=201)
def create_org(payload:OrgInput, db:Session=Depends(get_db), admin:User=Depends(require_admin)):
    row=Organization(**payload.model_dump());db.add(row);db.flush()
    audit(db,admin,'organization.created','organization',row.id)
    db.commit();return org_dict(db,row)


@router.put('/admin/organizations/{org_id}')
def update_org(org_id:int, payload:OrgInput, db:Session=Depends(get_db), admin:User=Depends(require_admin)):
    row=lock_org(db,org_id)
    for key,value in payload.model_dump().items():setattr(row,key,value)
    audit(db,admin,'organization.updated','organization',row.id,fields=list(payload.model_fields_set))
    # Existing data survives a reduced limit. New operations remain blocked until usage fits.
    db.commit();return org_dict(db,row)


@router.get('/admin/users')
def users(q:str=Query('',max_length=100), organization_id:int|None=None, offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),
          db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    query=db.query(User)
    if q:query=query.filter(User.email.icontains(q,autoescape=True) | User.full_name.icontains(q,autoescape=True))
    if organization_id is not None:query=query.filter(User.organization_id==organization_id)
    return {'total':query.count(),'items':[user_dict(u) for u in query.order_by(User.id.desc()).offset(offset).limit(limit)]}


def change_user(db, actor, target, payload):
    identities=lock_members(db, [actor.id, target.id], extra_org_ids=(payload.organization_id,),
        allow_inactive=True, expected_auth_versions={actor.id: actor.auth_version})
    actor,target=identities[actor.id],identities[target.id]
    if not actor.is_admin:
        require_team_owner(actor,db)
    if not actor.is_admin and target.organization_id!=actor.organization_id:
        raise HTTPException(403,"Недоступная организация")
    if target.id==actor.id or target.is_admin:
        raise HTTPException(409,'Изменение доступа владельца через эту форму запрещено')
    old_org=target.organization_id
    if payload.organization_role == 'owner' and target.organization_role != 'owner':
        raise HTTPException(403, 'Новый владелец должен принять персональное приглашение или предложение передачи')
    if not actor.is_admin:
        if payload.organization_role == 'owner' and target.organization_role != 'owner':
            raise HTTPException(403, 'Используйте подтверждаемую передачу владения')
        if actor.organization_role != 'owner' and (payload.organization_role in {'owner','admin'} or target.organization_role in {'owner','admin'}):
            raise HTTPException(403, 'Только владелец управляет администраторами')
    orgs={oid:db.get(Organization,oid) for oid in {old_org,payload.organization_id} if oid}
    leaving_owner = target.organization_role=='owner' and target.is_active and old_org and (
        not payload.is_active or payload.organization_id!=old_org or payload.organization_role!='owner')
    if leaving_owner and db.query(User).filter(User.organization_id==old_org, User.organization_role=='owner', User.is_active.is_(True), User.id!=target.id).count()==0:
        raise HTTPException(409,'Сначала назначьте другого администратора организации')
    if payload.organization_id and payload.is_active and (not target.is_active or payload.organization_id!=old_org):
        check_seat(db,orgs[payload.organization_id])
    if old_org and payload.organization_id != old_org:
        # Content and history stay with the original organization. A new tenant
        # never inherits the employee's former company's materials or answers.
        db.query(Course).filter(Course.owner_id==target.id, Course.organization_id.is_(None)).update({'organization_id':old_org})
        from app.models.learning import LearningRoleGrant, LearningGroupMember, LearningGroup, LearningPerson
        db.query(LearningRoleGrant).filter_by(user_id=target.id, organization_id=old_org).delete()
        old_groups=select(LearningGroup.id).where(LearningGroup.organization_id==old_org)
        db.query(LearningGroupMember).filter(LearningGroupMember.user_id==target.id, LearningGroupMember.group_id.in_(old_groups)).delete(synchronize_session=False)
        person=db.get(LearningPerson,target.id)
        if person:db.delete(person)
    target.is_active=payload.is_active;target.organization_id=payload.organization_id
    target.organization_role=payload.organization_role if payload.organization_id else 'member'
    target.auth_version+=1
    audit(db,actor,'user.access_changed','user',target.id,organization_id=target.organization_id,role=target.organization_role,is_active=target.is_active)
    db.commit();return user_dict(target)


@router.put('/admin/users/{user_id}')
def update_user(user_id:int,payload:UserChange,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    target=db.get(User,user_id)
    if not target:raise HTTPException(404,'Пользователь не найден')
    return change_user(db,admin,target,payload)


@router.post('/admin/users/{user_id}/recovery-link')
def recovery(user_id:int,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    target=db.get(User,user_id)
    if not target:raise HTTPException(404,'Пользователь не найден')
    if target.is_admin or not target.is_active:raise HTTPException(409,'Восстановление недоступно для этого аккаунта')
    db.query(PasswordReset).filter(PasswordReset.user_id==target.id,PasswordReset.consumed_at.is_(None)).update({'consumed_at':datetime.utcnow()})
    raw=secrets.token_urlsafe(32)
    db.add(PasswordReset(token_hash=hashlib.sha256(raw.encode()).hexdigest(),user_id=target.id,expires_at=datetime.utcnow()+timedelta(minutes=30)))
    audit(db,admin,'user.recovery_issued','user',target.id);db.commit()
    return {'url':settings.PUBLIC_APP_URL.rstrip('/')+'/reset-password#token='+raw,'expires_in_minutes':30}


@router.post('/admin/invitations',status_code=201)
def invite(payload:InviteInput,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    return make_invitation(db,admin,payload)


@router.get('/admin/invitations')
def invitations(organization_id:int|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    query=db.query(Invitation)
    if organization_id is not None:query=query.filter(Invitation.organization_id==organization_id)
    return {'total':query.count(),'items':[invitation_dict(r) for r in query.order_by(Invitation.id.desc()).offset(offset).limit(limit)]}


def revoke_invite(db,actor,row):
    actor=lock_member(db,actor.id,extra_org_ids=(row.organization_id,),expected_auth_version=actor.auth_version)
    if not actor.is_admin and require_team_owner(actor,db)!=row.organization_id:
        raise HTTPException(403,'Недоступная организация')
    changed=db.execute(update(Invitation).where(Invitation.id==row.id,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))
    if changed.rowcount!=1:raise HTTPException(409,'Приглашение уже использовано или отозвано')
    audit(db,actor,'invitation.revoked','invitation',row.id);db.commit();return {'ok':True}


@router.delete('/admin/invitations/{invite_id}')
def revoke(invite_id:int,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    row=db.get(Invitation,invite_id)
    if not row:raise HTTPException(404,'Приглашение не найдено')
    return revoke_invite(db,admin,row)


def valid_invitation(db,token):
    row=db.query(Invitation).filter(Invitation.token_hash==hashlib.sha256(token.encode()).hexdigest()).populate_existing().first()
    if not row or row.accepted_at or row.revoked_at or row.expires_at<=datetime.utcnow():
        raise HTTPException(410,'Приглашение истекло, отозвано или уже использовано. Запросите новую ссылку у администратора.')
    org=db.get(Organization,row.organization_id)
    if not org or not org.is_active:raise HTTPException(409,'Подключение к организации приостановлено')
    return row,org


@router.post('/auth/invitations/inspect')
def inspect_invite(payload:TokenInput,db:Session=Depends(get_db)):
    row,org=valid_invitation(db,payload.token)
    return {'email':row.email,'organization_name':org.name,'role':row.role,'expires_at':row.expires_at,'existing_account':bool(db.query(User).filter_by(email=row.email).first())}


@router.post('/auth/invitations/accept',response_model=AuthResponse,status_code=201)
def accept_invite(payload:InviteAccept,db:Session=Depends(get_db)):
    if not payload.terms_accepted:raise HTTPException(422,'Примите пользовательское соглашение')
    if not payload.full_name.strip():raise HTTPException(422,'Укажите имя')
    require_current_version(payload.legal_version)
    row,org=valid_invitation(db,payload.token)
    lock_org(db,org.id)
    row,org=valid_invitation(db,payload.token)
    if seats(db,org.id)>org.seat_limit:raise HTTPException(409,'Лимит участников изменился. Обратитесь к администратору.')
    if db.query(User).filter(User.email==row.email).first():raise HTTPException(409,'Аккаунт уже существует. Войдите с прежним паролем и обратитесь к администратору.')
    claimed=db.execute(update(Invitation).where(Invitation.id==row.id,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None),Invitation.expires_at>datetime.utcnow()).values(accepted_at=datetime.utcnow()))
    if claimed.rowcount!=1:raise HTTPException(410,'Приглашение уже использовано')
    user=User(email=row.email,password_hash=hash_password(payload.password),full_name=payload.full_name.strip(),organization_id=org.id,organization_role=row.role)
    db.add(user)
    try:
        db.flush()
        record_receipt(db,email=user.email,user_id=user.id,slug='terms',context='invitation')
        if payload.marketing_accepted:record_receipt(db,email=user.email,user_id=user.id,slug='marketing-consent',context='invitation')
        audit(db,user,'invitation.accepted','invitation',row.id,organization_id=org.id)
        db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Аккаунт уже существует или приглашение использовано')
    return AuthResponse(access_token=create_access_token(user.id),user=UserResponse.model_validate(user))


@router.get('/team')
def team(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if not user.organization_id:return {'organization':None,'members':[],'invitations':[],'can_manage':False}
    org=db.get(Organization,user.organization_id)
    from app.services.learning_service import is_manager
    owner=is_manager(db,user)
    members=db.query(User).filter(User.organization_id==org.id).order_by(User.id).all()
    # Every member sees the team; only managers receive invitation addresses.
    pending=db.query(Invitation).filter(Invitation.organization_id==org.id).order_by((Invitation.accepted_at.is_(None) & Invitation.revoked_at.is_(None) & (Invitation.expires_at > datetime.utcnow())).desc(), Invitation.id.desc()).limit(100).all() if owner else []
    return {'organization':org_dict(db,org),'members':[user_dict(m) for m in members],
        'invitations':[invitation_dict(i) for i in pending],'can_manage':owner}


@router.post('/auth/invitations/accept-existing', response_model=AuthResponse)
def accept_existing_invite(payload:TokenInput, request:Request, db:Session=Depends(get_db), user:User=Depends(get_current_user)):
    row,org=valid_invitation(db,payload.token)
    user=lock_member(db,user.id,extra_org_ids=(org.id,),expected_auth_version=user.auth_version)
    from app.services.mfa_service import authenticated_factor
    authenticated_factor(db,user,request.state.auth_claims)
    row,org=valid_invitation(db,payload.token)
    if normalize_email(user.email)!=row.email:
        raise HTTPException(403,'Приглашение адресовано другому аккаунту')
    if user.is_admin or user.organization_id:
        raise HTTPException(409,'Аккаунт уже подключён к организации или является оператором')
    if seats(db,org.id)>org.seat_limit:
        raise HTTPException(409,'Лимит участников изменился. Обратитесь к администратору.')
    claimed=db.execute(update(Invitation).where(Invitation.id==row.id,Invitation.accepted_at.is_(None),Invitation.revoked_at.is_(None),Invitation.expires_at>datetime.utcnow()).values(accepted_at=datetime.utcnow()))
    if claimed.rowcount!=1:raise HTTPException(410,'Приглашение уже использовано')
    user.organization_id=org.id
    user.organization_role=row.role
    user.auth_version+=1
    audit(db,user,'invitation.accepted_existing','invitation',row.id,organization_id=org.id)
    from app.services.mfa_service import preserve_token
    result=AuthResponse(access_token=preserve_token(user,request.state.auth_claims),user=UserResponse.model_validate(user))
    db.commit()
    return result


@router.post('/team/invitations/{invite_id}/resend')
def resend_invite(invite_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    org_id=require_team_owner(user,db)
    user=lock_member(db,user.id,extra_org_ids=(org_id,),expected_auth_version=user.auth_version)
    if require_team_owner(user,db)!=org_id:
        raise HTTPException(403,'Недоступная организация')
    row=db.query(Invitation).filter_by(id=invite_id,organization_id=org_id).first()
    if not row:raise HTTPException(404,'Приглашение не найдено')
    if row.accepted_at:raise HTTPException(409,'Приглашение уже принято')
    row.revoked_at=datetime.utcnow()
    db.flush()
    return make_invitation(db,user,InviteInput(email=row.email,organization_id=org_id,role=row.role))


@router.post('/team/invitations',status_code=201)
def team_invite(payload:InviteInput,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if payload.organization_id!=require_team_owner(user,db):raise HTTPException(403,'Недоступная организация')
    return make_invitation(db,user,payload)


@router.delete('/team/invitations/{invite_id}')
def team_revoke(invite_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    org_id=require_team_owner(user,db)
    row=db.query(Invitation).filter(Invitation.id==invite_id,Invitation.organization_id==org_id).first()
    if not row:raise HTTPException(404,'Приглашение не найдено')
    return revoke_invite(db,user,row)


@router.put('/team/members/{user_id}')
def team_member(user_id:int,payload:UserChange,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    org_id=require_team_owner(user,db)
    if payload.organization_id!=org_id:raise HTTPException(403,'Перенос между организациями доступен только администратору Лерниума')
    target=db.query(User).filter(User.id==user_id,User.organization_id==org_id).first()
    if not target:raise HTTPException(404,'Участник не найден')
    return change_user(db,user,target,payload)


def overview_data(db,owner_ids=None):
    start=datetime.utcnow()-timedelta(days=30)
    courses=db.query(Course).filter(Course.is_deleted.is_(False))
    documents=db.query(Document).filter(Document.is_deleted.is_(False),Document.is_current.is_(True))
    runs=db.query(GenerationRun).filter(GenerationRun.is_deleted.is_(False),GenerationRun.created_at>=start)
    if owner_ids is not None:
        courses=courses.filter(Course.owner_id.in_(owner_ids));documents=documents.filter(Document.owner_id.in_(owner_ids));runs=runs.filter(GenerationRun.owner_id.in_(owner_ids))
    run_ids=runs.with_entities(GenerationRun.id).subquery()
    tokens=db.query(func.coalesce(func.sum(AICall.input_tokens),0),func.coalesce(func.sum(AICall.output_tokens),0),func.coalesce(func.sum(AICall.cached_tokens),0)).filter(AICall.run_id.in_(select(run_ids.c.id))).one()
    cost=runs.with_entities(func.sum(GenerationRun.cost_usd)).scalar()
    recent=courses.order_by(Course.updated_at.desc(),Course.id.desc()).limit(6).all()
    history=runs.order_by(GenerationRun.id.desc()).limit(30).all()
    daily=runs.with_entities(func.date(GenerationRun.created_at),func.count(GenerationRun.id)).group_by(func.date(GenerationRun.created_at)).order_by(func.date(GenerationRun.created_at)).all()
    return {'metrics':{'courses':courses.count(),'published':courses.filter(Course.publication_status=='published').count(),'documents':documents.count(),
        'runs':runs.count(),'completed':runs.filter(GenerationRun.status.in_(['completed','succeeded'])).count(),
        'failed':runs.filter(GenerationRun.status=='failed').count(),'active':runs.filter(GenerationRun.status.in_(['queued','running','paused'])).count(),
        'input_tokens':tokens[0],'output_tokens':tokens[1],'cached_tokens':tokens[2],'known_cost_usd':float(cost) if cost is not None else None},
        'courses':[{'id':c.id,'title':c.name,'status':c.status,'publication_status':c.publication_status} for c in recent],
        'runs':[{'id':r.id,'course_id':r.course_id,'status':r.status,'progress_percent':r.progress_percent,'stage':r.current_stage,'created_at':r.created_at,'error_message':r.error_message} for r in history],
        'daily':[{'date':str(day),'count':count} for day,count in daily]}


@router.get('/workspace/overview')
def overview(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return overview_data(db,[user.id])


@router.get('/admin/overview')
def admin_overview(db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    return {**overview_data(db),'users':db.query(User).count(),'organizations':db.query(Organization).count(),
        'open_requests':db.query(LegalRequest).filter(LegalRequest.status=='open').count(),
        'unassigned_courses':db.query(Course).filter(Course.owner_id.is_(None),Course.is_deleted.is_(False)).count()}


@router.get('/admin/courses')
def admin_courses(unassigned:bool=False,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    query=db.query(Course).filter(Course.is_deleted.is_(False))
    if unassigned:query=query.filter(Course.owner_id.is_(None))
    return {'total':query.count(),'items':[{'id':c.id,'title':c.name,'owner_id':c.owner_id,'status':c.status,'publication_status':c.publication_status} for c in query.order_by(Course.id.desc()).offset(offset).limit(limit)]}


class AssignOwner(BaseModel):
    user_id:int=Field(gt=0)


@router.post('/admin/courses/{course_id}/assign-owner')
def assign_owner(course_id:int,payload:AssignOwner,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    course=db.execute(select(Course).where(Course.id==course_id,Course.is_deleted.is_(False)).with_for_update()).scalar_one_or_none()
    if not course:raise HTTPException(404,'Курс не найден')
    if course.owner_id is not None:raise HTTPException(409,'У курса уже есть владелец. Перенос чужих материалов через эту операцию запрещён.')
    user=db.get(User,payload.user_id)
    if not user or not user.is_active:raise HTTPException(422,'Выберите действующий аккаунт')
    if any(db.query(model).filter(model.course_id==course.id).first() for model in [Document, GenerationRun, CourseGraph, Chat]):
        raise HTTPException(409,'Курс содержит связанные данные. Нужен отдельный перенос с проверкой владельцев.')
    check_course_limit(db,user.id)
    course.owner_id=user.id;audit(db,admin,'course.owner_assigned','course',course.id,user_id=user.id);db.commit()
    return {'ok':True}


@router.get('/admin/requests')
def requests(status:Literal['open','closed']='open',offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    query=db.query(LegalRequest).filter(LegalRequest.status==status)
    return {'total':query.count(),'items':[{key:getattr(r,key) for key in ['reference','purpose','email','name','message','status','created_at','closed_at']} for r in query.order_by(LegalRequest.created_at.desc()).offset(offset).limit(limit)]}


@router.post('/admin/requests/{reference}/close')
def close_request(reference:str,db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    row=db.get(LegalRequest,reference)
    if not row:raise HTTPException(404,'Обращение не найдено')
    if row.status!='closed':
        row.status='closed';row.closed_at=datetime.utcnow();audit(db,admin,'request.closed','request',reference)
    db.commit();return {'ok':True}


@router.get('/admin/audit')
def audit_log(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),admin:User=Depends(require_admin)):
    query=db.query(AdminEvent)
    return {'total':query.count(),'items':[{key:getattr(r,key) for key in ['id','actor_id','action','target_type','target_id','details','created_at']} for r in query.order_by(AdminEvent.id.desc()).offset(offset).limit(limit)]}
