"""Explicit CRUD for operator-managed metadata, with reversible deletion."""
from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.administration import AdminEvent, Invitation
from app.models.course import Course
from app.models.legal import LegalRequest
from app.models.user import User
from app.services.auth_service import require_admin
from app.services.organization_access import audit, lock_member, lock_org
from app.services.admin_management import (
    ORG_FIELDS, COURSE_FIELDS, REQUEST_FIELDS, check_state, state_token,
    course_out, locked_course, require_course_idle, owner_for_new_course,
)
from app.routes.administration import (
    InviteInput, UserChange, make_invitation, change_user, org_dict, user_dict,
)

router = APIRouter(prefix='/admin')


@router.get('/management-capabilities')
def capabilities(actor=Depends(require_admin)):
    return {'version': 1, 'concurrency_required': True, 'deletion': 'archive', 'immutable_history': True}


class StateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_state: str = Field(min_length=64, max_length=64)


class AccountState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_auth_version: int = Field(ge=0)


class CourseCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    owner_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default='', max_length=10000)


class CourseEdit(StateInput):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default='', max_length=10000)


class ReissueInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    locale: Literal['ru', 'en'] = 'ru'
    role: Literal['owner', 'admin', 'author', 'reviewer', 'manager', 'member', 'candidate'] | None = None


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    purpose: Literal['enquiry', 'support', 'rights'] = 'support'
    name: str = Field(default='', max_length=160)
    email: str = Field(min_length=5, max_length=255)
    message: str = Field(min_length=10, max_length=5000)

    @field_validator('email')
    @classmethod
    def email_valid(cls, value):
        from app.schemas.auth import AuthCredentials
        return AuthCredentials.valid_email(value)


class RequestEdit(StateInput):
    status: Literal['open', 'closed']
    note: str = Field(default='', max_length=2000)


def request_out(db, row):
    notes = db.query(AdminEvent).filter_by(target_type='request', target_id=row.reference,
        action='request.note_added').order_by(AdminEvent.id).all()
    return {**{key: getattr(row, key) for key in ['reference', 'purpose', 'name', 'email', 'message', 'status', 'created_at', 'closed_at']},
            'state_token': state_token(row, REQUEST_FIELDS),
            'notes': [{'id': note.id, 'actor_id': note.actor_id, 'created_at': note.created_at,
                       'text': note.details.get('note', '')} for note in notes]}


@router.get('/organizations/{org_id}')
def organization_detail(org_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    from app.models.administration import Organization
    row = db.get(Organization, org_id)
    if not row:
        raise HTTPException(404, 'Организация не найдена')
    return org_dict(db, row)


@router.delete('/organizations/{org_id}')
@router.post('/organizations/{org_id}/archive')
def archive_organization(org_id: int, payload: StateInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    return organization_state(db, actor, org_id, payload, False)


@router.post('/organizations/{org_id}/restore')
def restore_organization(org_id: int, payload: StateInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    return organization_state(db, actor, org_id, payload, True)


def organization_state(db, actor, org_id, payload, active):
    lock_member(db, actor.id, extra_org_ids=(org_id,), expected_auth_version=actor.auth_version)
    row = lock_org(db, org_id)
    check_state(row, ORG_FIELDS, payload.expected_state)
    row.is_active = active
    audit(db, actor, 'organization.restored' if active else 'organization.archived', 'organization', row.id)
    db.commit()
    return {**org_dict(db, row), 'history_preserved': True}


@router.get('/users/{user_id}')
def user_detail(user_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(404, 'Пользователь не найден')
    return user_dict(row)


@router.post('/users', status_code=201)
def provision_user(payload: InviteInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    # Account creation, password choice and consent remain with the recipient.
    return {**make_invitation(db, actor, payload), 'resource_type': 'invitation', 'account_created': False}


@router.delete('/users/{user_id}')
@router.post('/users/{user_id}/archive')
def archive_user(user_id: int, payload: AccountState, db: Session = Depends(get_db), actor=Depends(require_admin)):
    return account_state(db, actor, user_id, payload, False)


@router.post('/users/{user_id}/restore')
def restore_user(user_id: int, payload: AccountState, db: Session = Depends(get_db), actor=Depends(require_admin)):
    return account_state(db, actor, user_id, payload, True)


def account_state(db, actor, user_id, payload, active):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(404, 'Пользователь не найден')
    return change_user(db, actor, row, UserChange(is_active=active, organization_id=row.organization_id,
        organization_role=row.organization_role, expected_auth_version=payload.expected_auth_version))


@router.post('/invitations/{invite_id}/resend', status_code=201)
def reissue_invitation(invite_id: int, payload: ReissueInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = db.get(Invitation, invite_id)
    if not row:
        raise HTTPException(404, 'Приглашение не найдено')
    lock_member(db, actor.id, extra_org_ids=(row.organization_id,), expected_auth_version=actor.auth_version)
    row = db.query(Invitation).filter_by(id=invite_id).with_for_update().populate_existing().one()
    if row.accepted_at:
        raise HTTPException(409, 'Приглашение уже принято. Измените доступ существующего пользователя.')
    row.revoked_at = row.revoked_at or datetime.utcnow()
    db.flush()
    audit(db, actor, 'invitation.reissued', 'invitation', row.id)
    # One transaction: failure to reserve a seat leaves the prior invitation valid.
    return make_invitation(db, actor, InviteInput(email=row.email, organization_id=row.organization_id,
        role=payload.role or row.role, locale=payload.locale,
        manager_id=row.manager_id if (payload.role or row.role) in {'member', 'candidate'} else None),
        position_id=row.position_id if (payload.role or row.role) in {'member', 'candidate'} else None)


@router.post('/courses', status_code=201)
def create_course(payload: CourseCreate, db: Session = Depends(get_db), actor=Depends(require_admin)):
    owner = owner_for_new_course(db, actor, payload.owner_id)
    row = Course(owner_id=owner.id, organization_id=owner.organization_id, name=payload.title,
                 description=payload.description, status='draft', publication_status='draft')
    db.add(row)
    db.flush()
    audit(db, actor, 'course.created_by_operator', 'course', row.id, owner_id=owner.id, organization_id=row.organization_id)
    db.commit()
    return course_out(row)


@router.get('/courses/{course_id}')
def course_detail(course_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = db.get(Course, course_id)
    if not row:
        raise HTTPException(404, 'Курс не найден')
    # Metadata only. Lesson content still requires author or explicit support scope.
    return course_out(row)


@router.put('/courses/{course_id}')
def edit_course(course_id: int, payload: CourseEdit, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = locked_course(db, actor, course_id)
    check_state(row, COURSE_FIELDS, payload.expected_state)
    if row.is_deleted:
        raise HTTPException(409, 'Сначала восстановите курс из архива')
    require_course_idle(db, row)
    if (row.name, row.description or '') != (payload.title, payload.description):
        from app.services.course_publication_service import CoursePublicationService
        revision = row.content_revision
        CoursePublicationService.prepare_for_edit(row)
        if row.content_revision == revision:
            row.content_revision += 1
        row.name, row.description = payload.title, payload.description
        audit(db, actor, 'course.metadata_updated', 'course', row.id, revision=row.content_revision)
    db.commit()
    return course_out(row)


@router.delete('/courses/{course_id}')
@router.post('/courses/{course_id}/archive')
def archive_course(course_id: int, payload: StateInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    return course_state(db, actor, course_id, payload, True)


@router.post('/courses/{course_id}/restore')
def restore_course(course_id: int, payload: StateInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    return course_state(db, actor, course_id, payload, False)


def course_state(db, actor, course_id, payload, archived):
    row = locked_course(db, actor, course_id)
    check_state(row, COURSE_FIELDS, payload.expected_state)
    require_course_idle(db, row)
    if not archived and row.is_deleted:
        owner = db.get(User, row.owner_id) if row.owner_id else None
        if not owner or not owner.is_active or owner.organization_id != row.organization_id:
            raise HTTPException(409, 'Для восстановления нужен действующий владелец в исходном пространстве курса')
        from app.services.organization_access import check_course_limit
        check_course_limit(db, owner.id)
    row.is_deleted = archived
    audit(db, actor, 'course.archived' if archived else 'course.restored', 'course', row.id,
          organization_id=row.organization_id, published_versions_preserved=True)
    db.commit()
    return {**course_out(row), 'history_preserved': True}


@router.post('/requests', status_code=201)
def record_request(payload: RequestCreate, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = LegalRequest(reference=str(uuid4()), **payload.model_dump(), status='open')
    db.add(row)
    db.flush()
    # Recording an incoming message is not a consent receipt or an outgoing message.
    audit(db, actor, 'request.recorded_by_operator', 'request', row.reference)
    db.commit()
    return request_out(db, row)


@router.get('/requests/{reference}')
def request_detail(reference: str, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = db.get(LegalRequest, reference)
    if not row:
        raise HTTPException(404, 'Обращение не найдено')
    return request_out(db, row)


@router.put('/requests/{reference}')
def edit_request(reference: str, payload: RequestEdit, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = db.query(LegalRequest).filter_by(reference=reference).with_for_update().first()
    if not row:
        raise HTTPException(404, 'Обращение не найдено')
    check_state(row, REQUEST_FIELDS, payload.expected_state)
    row.status = payload.status
    row.closed_at = datetime.utcnow() if payload.status == 'closed' else None
    audit(db, actor, 'request.closed' if row.status == 'closed' else 'request.reopened', 'request', reference)
    if payload.note.strip():
        audit(db, actor, 'request.note_added', 'request', reference, note=payload.note.strip())
    db.commit()
    return request_out(db, row)


@router.delete('/requests/{reference}')
@router.post('/requests/{reference}/archive')
def archive_request(reference: str, payload: StateInput, db: Session = Depends(get_db), actor=Depends(require_admin)):
    row = db.query(LegalRequest).filter_by(reference=reference).with_for_update().first()
    if not row:
        raise HTTPException(404, 'Обращение не найдено')
    check_state(row, REQUEST_FIELDS, payload.expected_state)
    if row.status == 'open':
        raise HTTPException(409, 'Сначала обработайте обращение. Открытые запросы нельзя скрыть в архиве.')
    row.status = 'archived'
    audit(db, actor, 'request.archived', 'request', reference)
    db.commit()
    return request_out(db, row)
