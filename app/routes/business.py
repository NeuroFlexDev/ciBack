"""Company owner → manager → job profile → employee → learning assignment."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.administration import Invitation
from app.models.business import BusinessPosition
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.learning import LearningPerson
from app.models.user import User
from app.schemas.course_generation_settings import CourseGenerationSettingsUpdate
from app.schemas.learning import LearningRules
from app.services.auth_service import get_current_user
from app.services.business_service import require_business, position_for
from app.services.organization_access import audit, check_course_limit, check_seat, lock_members
from app.services import learning_service as learning

router = APIRouter(prefix='/business')


class PositionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=3000)
    responsibilities: str = Field(default='', max_length=6000)
    competencies: str = Field(default='', max_length=4000)


class PositionUpdate(PositionInput):
    expected_revision: int = Field(ge=1)


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)


class PositionState(RevisionInput):
    is_archived: bool


class EmployeePosition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    position_id: int | None = Field(default=None, gt=0)
    expected_position_id: int | None = Field(gt=0)


class ManagerInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['manager', 'member']
    expected_auth_version: int = Field(ge=0)


class EmployeeAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')
    is_active: StrictBool
    expected_auth_version: int = Field(ge=0)


class InviteEmployee(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=5, max_length=255)
    role: Literal['manager', 'member'] = 'member'
    position_id: int | None = Field(default=None, gt=0)
    locale: Literal['ru', 'en'] = 'ru'

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        from app.schemas.auth import AuthCredentials
        return AuthCredentials.valid_email(value)


class PositionCourse(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    lesson_count: int = Field(default=6, ge=1, le=100)
    language: Literal['ru', 'en'] = 'ru'
    expected_position_revision: int = Field(ge=1)


class PositionRegulation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    language: Literal['ru', 'en'] = 'ru'
    expected_position_revision: int = Field(ge=1)


def position_out(db, row):
    employees = db.query(func.count(User.id)).join(LearningPerson, LearningPerson.user_id == User.id).filter(
        User.organization_id == row.organization_id, User.is_active.is_(True), LearningPerson.position_id == row.id).scalar()
    courses = db.query(func.count(Course.id)).filter_by(organization_id=row.organization_id, position_id=row.id, is_deleted=False).scalar()
    return {**{k: getattr(row, k) for k in ['id', 'name', 'description', 'responsibilities', 'competencies', 'revision', 'is_archived']},
            'employee_count': employees, 'course_count': courses}


def revision(row, expected):
    if row.revision != expected:
        raise HTTPException(409, 'Должность уже изменена. Обновите карточку перед сохранением.')


def unique_name(db, org_id, name, except_id=None):
    key = name.casefold()
    query = db.query(BusinessPosition).filter_by(organization_id=org_id, name_key=key)
    if except_id is not None:
        query = query.filter(BusinessPosition.id != except_id)
    if query.first():
        raise HTTPException(409, 'Такая должность уже существует, в том числе в архиве')
    return key


@router.get('/positions')
def positions(archived: bool = False, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
              db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    query = db.query(BusinessPosition).filter_by(organization_id=org.id, is_archived=archived)
    return {'total': query.count(), 'items': [position_out(db, p) for p in query.order_by(BusinessPosition.name, BusinessPosition.id).offset(offset).limit(limit)]}


@router.post('/positions', status_code=201)
def create_position(payload: PositionInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    row = BusinessPosition(**payload.model_dump(), name_key=unique_name(db, org.id, payload.name), organization_id=org.id, created_by=user.id)
    db.add(row); db.flush()
    audit(db, user, 'business.position_created', 'position', row.id, organization_id=org.id)
    db.commit()
    return position_out(db, row)


@router.get('/positions/{position_id}')
def get_position(position_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    return position_out(db, position_for(db, org.id, position_id, active=False))


@router.put('/positions/{position_id}')
def update_position(position_id: int, payload: PositionUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    row = position_for(db, org.id, position_id)
    revision(row, payload.expected_revision)
    row.name_key = unique_name(db, org.id, payload.name, row.id)
    for key, value in payload.model_dump(exclude={'expected_revision'}).items():
        setattr(row, key, value)
    row.revision += 1
    db.query(LearningPerson).filter_by(organization_id=org.id, position_id=row.id).update({'position': row.name})
    audit(db, user, 'business.position_updated', 'position', row.id, organization_id=org.id, revision=row.revision)
    db.commit()
    return position_out(db, row)


@router.put('/positions/{position_id}/state')
def archive_position(position_id: int, payload: PositionState,
                     db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    row = position_for(db, org.id, position_id, active=False)
    revision(row, payload.expected_revision)
    action = 'archive' if payload.is_archived else 'restore'
    row.is_archived = payload.is_archived
    row.revision += 1
    if row.is_archived:
        # A pending invitation must not strand its recipient on an archived job.
        db.query(Invitation).filter_by(organization_id=org.id, position_id=row.id, accepted_at=None, revoked_at=None).update({'revoked_at': datetime.utcnow()})
    audit(db, user, 'business.position_' + action, 'position', row.id, organization_id=org.id)
    db.commit()
    return position_out(db, row)


@router.get('/employees')
def employees(q: str = Query('', max_length=100), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
              db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    query = db.query(User).filter_by(organization_id=org.id)
    if q.strip():
        query = query.filter(User.email.icontains(q.strip(), autoescape=True) | User.full_name.icontains(q.strip(), autoescape=True))
    from app.routes.administration import user_dict
    rows = query.order_by(User.id).offset(offset).limit(limit).all()
    people = {p.user_id: p for p in db.query(LearningPerson).filter(LearningPerson.organization_id == org.id, LearningPerson.user_id.in_([r.id for r in rows]))}
    return {'total': query.count(), 'items': [user_dict(r) | {'position_id': people[r.id].position_id if r.id in people else None, 'position_name': people[r.id].position if r.id in people else ''} for r in rows]}


@router.put('/employees/{user_id}/position')
def assign_position(user_id: int, payload: EmployeePosition, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], expected_auth_versions={user.id: user.auth_version})
    org = require_business(db, identities[user.id])
    target = identities[user_id]
    if target.organization_id != org.id or target.is_admin or target.organization_role != 'member':
        raise HTTPException(404, 'Сотрудник не найден в компании')
    row = db.get(LearningPerson, target.id)
    if (row.position_id if row else None) != payload.expected_position_id:
        raise HTTPException(409, 'Должность сотрудника уже изменена. Обновите список.')
    position = position_for(db, org.id, payload.position_id) if payload.position_id else None
    if row is None:
        row = LearningPerson(user_id=target.id, organization_id=org.id)
        db.add(row)
    row.position_id, row.position = (position.id, position.name) if position else (None, '')
    audit(db, user, 'business.employee_position', 'user', target.id, organization_id=org.id, position_id=row.position_id)
    db.commit()
    return {'user_id': target.id, 'position_id': row.position_id}


@router.put('/employees/{user_id}/manager')
def appoint_manager(user_id: int, payload: ManagerInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], expected_auth_versions={user.id: user.auth_version})
    org = require_business(db, identities[user.id], owner=True)
    target = identities[user_id]
    if target.organization_id != org.id or target.is_admin or target.organization_role not in {'member', 'manager'}:
        raise HTTPException(404, 'Сотрудник или управляющий не найден')
    if target.auth_version != payload.expected_auth_version:
        raise HTTPException(409, 'Доступ сотрудника уже изменён. Обновите список.')
    target.organization_role = payload.role
    target.auth_version += 1
    person = db.get(LearningPerson, target.id)
    if person and payload.role == 'manager':
        person.position_id, person.position = None, ''
    audit(db, user, 'business.manager_changed', 'user', target.id, organization_id=org.id, role=payload.role)
    db.commit()
    return {'user_id': target.id, 'role': target.organization_role}


@router.put('/employees/{user_id}/access')
def employee_access(user_id: int, payload: EmployeeAccess, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], allow_inactive=True,
        expected_auth_versions={user.id: user.auth_version})
    actor, target = identities[user.id], identities[user_id]
    org = require_business(db, actor)
    if target.organization_id != org.id or target.is_admin or target.id == actor.id or target.organization_role not in {'member', 'manager'}:
        raise HTTPException(404, 'Сотрудник или управляющий не найден')
    if target.organization_role == 'manager' and actor.organization_role != 'owner':
        raise HTTPException(403, 'Доступом управляющего распоряжается владелец компании')
    if target.auth_version != payload.expected_auth_version:
        raise HTTPException(409, 'Доступ сотрудника уже изменён. Обновите список.')
    if payload.is_active and not target.is_active:
        check_seat(db, org)
    if target.is_active != payload.is_active:
        target.is_active = payload.is_active
        target.auth_version += 1
        audit(db, actor, 'business.employee_access', 'user', target.id, organization_id=org.id, is_active=target.is_active)
    db.commit()
    return {'user_id': target.id, 'is_active': target.is_active, 'auth_version': target.auth_version}


@router.post('/invitations', status_code=201)
def invite(payload: InviteEmployee, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, owner=payload.role == 'manager')
    from app.routes.administration import make_invitation, InviteInput
    return make_invitation(db, user, InviteInput(email=payload.email, organization_id=org.id, role=payload.role, locale=payload.locale), position_id=payload.position_id)


@router.get('/invitations')
def invitations(db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    from app.routes.administration import invitation_dict
    query = db.query(Invitation).filter_by(organization_id=org.id, accepted_at=None, revoked_at=None).filter(Invitation.expires_at > datetime.utcnow())
    if user.organization_role != 'owner':
        query = query.filter_by(role='member')
    return {'items': [invitation_dict(i) | {'position_id': i.position_id} for i in query.order_by(Invitation.id.desc()).limit(100)]}


@router.delete('/invitations/{invitation_id}')
def revoke_invitation(invitation_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    row = db.query(Invitation).filter_by(id=invitation_id, organization_id=org.id, accepted_at=None, revoked_at=None).first()
    if not row or (row.role != 'member' and user.organization_role != 'owner'):
        raise HTTPException(404, 'Приглашение не найдено')
    row.revoked_at = datetime.utcnow()
    audit(db, user, 'invitation.revoked', 'invitation', row.id, organization_id=org.id)
    db.commit()
    return {'ok': True}


@router.get('/positions/{position_id}/regulations')
@router.get('/positions/{position_id}/courses')
def position_courses(position_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                     db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    position_for(db, org.id, position_id, active=False)
    query = db.query(Course).filter_by(organization_id=org.id, position_id=position_id, is_deleted=False)
    return {'total': query.count(), 'items': [{k: getattr(c, k) for k in ['id', 'name', 'description', 'status', 'publication_status', 'content_revision']} for c in query.order_by(Course.id.desc()).offset(offset).limit(limit)]}


@router.post('/positions/{position_id}/courses', status_code=201)
def create_position_course(position_id: int, payload: PositionCourse, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return _create_position_material(position_id, payload, db, user)


@router.post('/positions/{position_id}/regulations', status_code=201)
def create_position_regulation(position_id: int, payload: PositionRegulation,
                               db: Session = Depends(get_db), user=Depends(get_current_user)):
    # A regulation is one document. Existing course storage and published
    # snapshots remain compatible; the manager never configures a curriculum.
    material = PositionCourse(**payload.model_dump(), lesson_count=1)
    return _create_position_material(position_id, material, db, user, regulation=True)


def _create_position_material(position_id, payload, db, user, *, regulation=False):
    org = require_business(db, user, lock=True)
    position = position_for(db, org.id, position_id)
    revision(position, payload.expected_position_revision)
    check_course_limit(db, user.id)
    course = Course(owner_id=user.id, organization_id=org.id, position_id=position.id, status='draft')
    db.add(course); db.flush()
    from app.services.course_generation_settings_service import CourseGenerationSettingsService
    settings = CourseGenerationSettingsUpdate(title=payload.title, goal=payload.goal, target_audience=position.name,
        difficulty='basic', language=payload.language, lesson_count=payload.lesson_count, module_tests_enabled=not regulation, final_test_enabled=True)
    CourseGenerationSettingsService.upsert(db, course.id, user.id, settings, commit=False)
    if regulation:
        # One editable document, created atomically with its initial history.
        # Keep the established storage so publication and assignments retain IDs.
        from app.models.module import Module
        from app.models.lesson import Lesson
        from app.models.theory import Theory
        from app.services.course_editor_service import CourseEditorService
        module = Module(course_id=course.id, title=payload.title, position=0, revision=1)
        db.add(module); db.flush()
        lesson = Lesson(module_id=module.id, title=payload.title, position=0, revision=1)
        db.add(lesson); db.flush()
        db.add(Theory(lesson_id=lesson.id, content='')); db.flush()
        CourseEditorService._module_snapshot(db, module, user.id)
        CourseEditorService._lesson_snapshot(db, lesson, user.id)
    audit(db, user, 'business.course_created', 'course', course.id, organization_id=org.id, position_id=position.id, position_revision=position.revision)
    db.commit()
    return {'course_id': course.id, 'position_id': position.id,
            'url': f'/express?course={course.id}&step=1&position={position.id}' if regulation else f'/express?course={course.id}&step=1'}


@router.post('/positions/{position_id}/regulations/{course_id}/assign', status_code=201)
@router.post('/positions/{position_id}/courses/{course_id}/assign', status_code=201)
def assign_course(position_id: int, course_id: int, payload: RevisionInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    position = position_for(db, org.id, position_id)
    revision(position, payload.expected_revision)
    course = db.query(Course).filter_by(id=course_id, organization_id=org.id, position_id=position.id, is_deleted=False).first()
    if not course or course.publication_status != 'published':
        raise HTTPException(409, 'Сначала опубликуйте регламент этой должности')
    version = db.query(CourseVersion).filter_by(course_id=course.id, publication_status='published').order_by(CourseVersion.revision.desc()).first()
    if not version:
        raise HTTPException(409, 'Опубликованная версия регламента не найдена')
    learning.snapshot_for(db, version.id)
    config = learning.settings_for(db, org.id)
    if config.features.get('learning') is False:
        raise HTTPException(403, 'Обучение не включено в поставку организации')
    targets = db.query(User).join(LearningPerson, LearningPerson.user_id == User.id).filter(
        User.organization_id == org.id, User.is_active.is_(True), User.organization_role == 'member', LearningPerson.position_id == position.id).all()
    if not targets:
        raise HTTPException(409, 'На эту должность ещё не назначены сотрудники')
    rules = LearningRules.model_validate(config.rules).model_dump()
    created = 0
    for target in targets:
        assignment, added = learning.create_assignment(db, org_id=org.id, version_id=version.id, user_id=target.id,
            actor_id=user.id, rules=rules, rules_revision=config.revision)
        created += int(added)
        if added:
            audit(db, user, 'learning.assigned', 'assignment', assignment.id, organization_id=org.id, user_id=target.id, course_version_id=version.id, position_id=position.id)
    db.commit()
    return {'created': created, 'already_assigned': len(targets) - created, 'total': len(targets)}
