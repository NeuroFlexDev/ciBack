"""Operator lifecycle actions preserve tenant content and immutable history."""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.models.course import Course
from app.models.generation_run import GenerationRun
from app.models.user import User
from app.services.organization_access import lock_members


def state_token(row, fields):
    values = {key: getattr(row, key) for key in fields}
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


ORG_FIELDS = ('id', 'name', 'kind', 'is_active', 'seat_limit', 'course_limit', 'source_limit', 'generation_limit', 'ai_unlimited')
COURSE_FIELDS = ('id', 'name', 'description', 'owner_id', 'organization_id', 'status', 'publication_status', 'content_revision', 'is_deleted')
REQUEST_FIELDS = ('reference', 'status', 'closed_at')


def check_state(row, fields, expected):
    if expected is not None and expected != state_token(row, fields):
        raise HTTPException(409, {'code': 'revision_conflict', 'message': 'Запись изменена другим оператором. Обновите данные и повторите действие.'})


def course_out(course):
    return {'id': course.id, 'title': course.name, 'description': course.description or '',
            'owner_id': course.owner_id, 'organization_id': course.organization_id,
            'status': course.status, 'publication_status': course.publication_status,
            'content_revision': course.content_revision, 'is_deleted': course.is_deleted,
            'state_token': state_token(course, COURSE_FIELDS)}


def locked_course(db, actor, course_id):
    initial = db.get(Course, course_id)
    if not initial:
        raise HTTPException(404, 'Курс не найден')
    # Organization -> users -> course is the same order as author/worker paths.
    owner_id, org_id = initial.owner_id, initial.organization_id
    lock_members(db, [actor.id] + ([owner_id] if owner_id else []), extra_org_ids=(org_id,),
                 allow_inactive=True, expected_auth_versions={actor.id: actor.auth_version})
    row = db.execute(select(Course).where(Course.id == course_id).with_for_update()
                     .execution_options(populate_existing=True)).scalar_one()
    if row.owner_id != owner_id or row.organization_id != org_id:
        raise HTTPException(409, 'Владелец курса изменился. Обновите данные.')
    return row


def require_course_idle(db, course):
    active = db.query(GenerationRun.id).filter(GenerationRun.course_id == course.id,
        GenerationRun.is_deleted.is_(False), GenerationRun.status.in_(['queued', 'running', 'paused'])).first()
    if course.status == 'generating' or active:
        raise HTTPException(409, 'Сначала завершите или отмените активный запуск курса. Его данные сохранены.')


def owner_for_new_course(db, actor, owner_id, *, extra_org_ids=()):
    identities = lock_members(db, [actor.id, owner_id], extra_org_ids=extra_org_ids,
                              expected_auth_versions={actor.id: actor.auth_version})
    owner = identities[owner_id]
    if owner.is_admin:
        raise HTTPException(422, 'Выберите аккаунт автора, а не оператора платформы')
    from app.services.organization_access import check_course_limit
    check_course_limit(db, owner.id)
    return owner
