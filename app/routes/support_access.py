"""Explicit support grants never add author permissions or impersonate members."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import re
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.administration import Organization
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.learning import SupportAccessGrant, LearningAssignment, LearningResponse
from app.models.user import User
from app.services.auth_service import get_current_user, require_admin
from app.services.organization_access import audit, lock_org
from app.services import learning_service as learning

router = APIRouter(prefix='/support')


class RequestInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    organization_id: int = Field(gt=0)
    scope_type: Literal['course', 'assignment']
    scope_id: int = Field(gt=0)
    reason: str = Field(min_length=10, max_length=2000)
    requested_hours: int = Field(default=1, ge=1, le=24)


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    decision: Literal['approved', 'rejected']
    hours: int = Field(default=1, ge=1, le=24)
    comment: str = Field(default='', max_length=2000)


class RevokeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)


def owner_org(user):
    if not user.organization_id or user.organization_role != 'owner' or user.is_admin:
        raise HTTPException(403, 'Разрешение поддержки подтверждает только владелец организации')
    return user.organization_id


def target(db, row):
    model = Course if row.scope_type == 'course' else LearningAssignment
    result = db.query(model).filter(model.id == row.scope_id, model.organization_id == row.organization_id).first()
    if result is None:
        raise HTTPException(404, 'Объект разрешения больше не доступен в этой организации')
    return result


def state(row):
    if row.status == 'approved' and (not row.expires_at or row.expires_at <= datetime.utcnow()):
        return 'expired'
    if row.status == 'pending' and row.created_at + timedelta(days=7) <= datetime.utcnow():
        return 'expired'
    return row.status


def out(db, row):
    operator = db.get(User, row.operator_id)
    result = {k: getattr(row, k) for k in ['id', 'organization_id', 'operator_id', 'scope_type', 'scope_id', 'reason', 'requested_hours', 'revision', 'created_at', 'decided_at', 'decided_by', 'decision_comment', 'expires_at', 'revoked_at']}
    # Approval cards contain scope metadata and purpose, never learning content.
    result.update(status=state(row), operator_name=operator.full_name or operator.email,
        organization_name=db.get(Organization, row.organization_id).name)
    return result


@router.get('/grants')
def list_grants(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user=Depends(get_current_user)):
    query = db.query(SupportAccessGrant)
    query = query.filter_by(operator_id=user.id) if user.is_admin else query.filter_by(organization_id=owner_org(user))
    return {'total': query.count(), 'items': [out(db, row) for row in query.order_by(SupportAccessGrant.id.desc()).offset(offset).limit(limit)]}


@router.post('/grants', status_code=201)
def request_access(payload: RequestInput, db: Session = Depends(get_db), user=Depends(require_admin)):
    org = lock_org(db, payload.organization_id)
    if not org.is_active:
        raise HTTPException(409, 'Организация приостановлена')
    row = SupportAccessGrant(**payload.model_dump(), operator_id=user.id)
    target(db, row)
    pending = db.query(SupportAccessGrant).filter_by(organization_id=org.id, operator_id=user.id, scope_type=payload.scope_type, scope_id=payload.scope_id, status='pending').all()
    if any(state(item) == 'pending' for item in pending):
        raise HTTPException(409, 'Для этого объекта уже ожидается решение владельца')
    db.add(row); db.flush()
    audit(db, user, 'support.requested', 'support_grant', row.id, organization_id=org.id,
        scope_type=row.scope_type, scope_id=row.scope_id, requested_hours=row.requested_hours)
    db.commit()
    return out(db, row)


@router.post('/grants/{grant_id}/decision')
def decide(grant_id: int, payload: DecisionInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org_id = owner_org(user)
    lock_org(db, org_id)
    row = db.query(SupportAccessGrant).filter_by(id=grant_id, organization_id=org_id).with_for_update().first()
    if not row:
        raise HTTPException(404, 'Запрос не найден')
    if row.revision != payload.expected_revision or state(row) != 'pending':
        raise HTTPException(409, 'Запрос уже изменён или истёк. Обновите список.')
    target(db, row)
    operator = db.get(User, row.operator_id)
    if not operator.is_active or not operator.is_admin:
        raise HTTPException(409, 'Оператор больше не имеет доступа к платформе')
    if payload.hours > row.requested_hours:
        raise HTTPException(422, 'Срок не может превышать запрошенный оператором')
    row.status = payload.decision
    row.decided_at, row.decided_by, row.decision_comment = datetime.utcnow(), user.id, payload.comment
    row.expires_at = row.decided_at + timedelta(hours=payload.hours) if row.status == 'approved' else None
    row.revision += 1
    audit(db, user, 'support.' + row.status, 'support_grant', row.id, organization_id=org_id, hours=payload.hours, operator_id=row.operator_id)
    db.commit()
    return out(db, row)


@router.post('/grants/{grant_id}/revoke')
def revoke(grant_id: int, payload: RevokeInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    query = db.query(SupportAccessGrant).filter_by(id=grant_id)
    query = query.filter_by(operator_id=user.id) if user.is_admin else query.filter_by(organization_id=owner_org(user))
    row = query.with_for_update().first()
    if not row:
        raise HTTPException(404, 'Разрешение не найдено')
    if row.revision != payload.expected_revision:
        raise HTTPException(409, 'Разрешение изменилось. Обновите список.')
    if row.status not in {'pending', 'approved'}:
        raise HTTPException(409, 'Разрешение уже закрыто')
    row.status, row.revoked_at, row.revoked_by = 'revoked', datetime.utcnow(), user.id
    row.revision += 1
    audit(db, user, 'support.revoked', 'support_grant', row.id, organization_id=row.organization_id)
    db.commit()
    return out(db, row)


def permitted(db, grant_id, user):
    row = db.query(SupportAccessGrant).filter_by(id=grant_id, operator_id=user.id).first()
    if not row or not user.is_admin or state(row) != 'approved':
        raise HTTPException(403, 'Нет действующего разрешения владельца на этот объект')
    if not db.get(Organization, row.organization_id).is_active:
        raise HTTPException(403, 'Организация приостановлена')
    return row, target(db, row)


def content_for(db, grant, obj):
    if grant.scope_type == 'course':
        snapshot = learning.build_publication_snapshot(db, obj, sorted((m for m in obj.modules if not m.is_deleted), key=lambda m: (m.position, m.id)))
    else:
        snapshot = deepcopy(learning.snapshot_for(db, obj.course_version_id))
    for item in learning.assessments(snapshot):
        item.pop('correct_answers', None)
    return snapshot


@router.get('/grants/{grant_id}/content')
def read_content(grant_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    grant, obj = permitted(db, grant_id, user)
    data = {'grant': out(db, grant), 'content': content_for(db, grant, obj), 'responses': []}
    if grant.scope_type == 'assignment':
        data['assignment'] = learning.assignment_summary(db, obj)
        rows = db.query(LearningResponse).filter_by(assignment_id=obj.id).order_by(LearningResponse.id).all()
        data['responses'] = [{k: getattr(row, k) for k in ['id', 'assessment_id', 'attempt', 'status', 'answer', 'points', 'feedback', 'submitted_at', 'revision']} for row in rows]
    audit(db, user, 'support.content_read', 'support_grant', grant.id, organization_id=grant.organization_id,
        scope_type=grant.scope_type, scope_id=grant.scope_id)
    db.commit()
    return data


@router.get('/grants/{grant_id}/images/{image_id}/content')
def read_image(grant_id: int, image_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    grant, obj = permitted(db, grant_id, user)
    snapshot = content_for(db, grant, obj)
    if not re.search(r'/api/images/' + str(image_id) + r'/content(?![\w/])', json.dumps(snapshot)):
        raise HTTPException(404, 'Изображение не относится к разрешённому объекту')
    from app.models.materials import CourseImage
    from app.services import image_service
    image = db.get(CourseImage, image_id)
    course_id = obj.id if grant.scope_type == 'course' else db.get(CourseVersion, obj.course_version_id).course_id
    if not image or image.course_id != course_id or not image.storage_key:
        raise HTTPException(404, 'Изображение не найдено')
    try:
        content = image_service.storage().read_bytes(image.storage_key)
    except (FileNotFoundError, OSError):
        raise HTTPException(404, 'Файл изображения недоступен') from None
    audit(db, user, 'support.image_read', 'support_grant', grant.id, organization_id=grant.organization_id, image_id=image_id)
    db.commit()
    return Response(content, media_type=image.mime_type, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
