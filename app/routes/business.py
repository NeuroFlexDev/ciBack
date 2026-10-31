"""Company owner → manager → job profile → employee → learning assignment."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator
from sqlalchemy.exc import IntegrityError
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
from app.services.business_hierarchy import people_filter, require_person, resolve_manager, LEARNER_ROLES, clear_reporting_lines
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


class EmployeeProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str = Field(min_length=1, max_length=160)
    expected_full_name: str = Field(max_length=160)

    @field_validator('full_name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Укажите имя участника')
        return value.strip()


class ReportingLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manager_id: int | None = Field(default=None, gt=0)
    expected_manager_id: int | None = Field(gt=0)
    expected_auth_version: int = Field(ge=0)


class LearnerRole(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['member', 'candidate']
    expected_auth_version: int = Field(ge=0)


class InviteEmployee(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(min_length=5, max_length=255)
    role: Literal['manager', 'member', 'candidate'] = 'member'
    manager_id: int | None = Field(default=None, gt=0)
    position_id: int | None = Field(default=None, gt=0)
    locale: Literal['ru', 'en'] = 'ru'

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        from app.schemas.auth import AuthCredentials
        return AuthCredentials.valid_email(value)


class CreateEmployee(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str = Field(min_length=1, max_length=160)
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=128)
    role: Literal['manager', 'member', 'candidate'] = 'member'
    manager_id: int | None = Field(default=None, gt=0)
    position_id: int | None = Field(default=None, gt=0)

    @field_validator('username')
    @classmethod
    def login_name(cls, value):
        from app.schemas.auth import valid_username
        return valid_username(value)

    @field_validator('full_name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Укажите имя сотрудника')
        return value.strip()


class EmployeePassword(BaseModel):
    model_config = ConfigDict(extra='forbid')
    new_password: SecretStr = Field(min_length=12, max_length=128)
    expected_auth_version: int = Field(ge=0)


@router.post('/employees', status_code=201)
def create_employee(payload: CreateEmployee, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, owner=payload.role == 'manager', lock=True)
    check_seat(db, org)
    manager_id = resolve_manager(db, user, org.id, payload.role, payload.manager_id)
    if payload.position_id and payload.role == 'manager':
        raise HTTPException(422, 'Должность назначается сотруднику или кандидату')
    position = position_for(db, org.id, payload.position_id) if payload.position_id else None
    if db.query(User.id).filter_by(username=payload.username).first():
        raise HTTPException(409, 'Этот логин уже занят. Выберите другой.')
    from app.core.security import hash_password
    from app.routes.administration import user_dict
    employee = User(email=None, username=payload.username, full_name=payload.full_name,
        password_hash=hash_password(payload.password.get_secret_value()), organization_id=org.id,
        organization_role=payload.role, manager_id=manager_id, is_admin=False, is_active=True)
    db.add(employee)
    try:
        db.flush()
        if position:
            db.add(LearningPerson(user_id=employee.id, organization_id=org.id, position_id=position.id, position=position.name))
        audit(db, user, 'business.employee_created', 'user', employee.id, organization_id=org.id, role=payload.role)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Этот логин уже занят. Выберите другой.') from None
    return user_dict(employee)


@router.post('/employees/{user_id}/password')
def employee_password(user_id: int, payload: EmployeePassword, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], expected_auth_versions={user.id: user.auth_version})
    actor, target = identities[user.id], identities[user_id]
    org = require_business(db, actor)
    require_person(db, actor, target)
    if not target.username or target.id == actor.id or target.organization_role not in {'member', 'candidate', 'manager'}:
        raise HTTPException(404, 'Аккаунт сотрудника не найден')
    if target.organization_role == 'manager' and actor.organization_role != 'owner':
        raise HTTPException(403, 'Доступом управляющего распоряжается владелец компании')
    if target.auth_version != payload.expected_auth_version:
        raise HTTPException(409, 'Аккаунт уже изменён. Обновите список.')
    from app.core.security import hash_password
    target.password_hash = hash_password(payload.new_password.get_secret_value())
    target.auth_version += 1
    audit(db, actor, 'business.employee_password_reset', 'user', target.id, organization_id=org.id)
    db.commit()
    return {'user_id': target.id, 'auth_version': target.auth_version}


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


def position_out(db, row, actor=None):
    people = db.query(User).join(LearningPerson, LearningPerson.user_id == User.id).filter(
        User.organization_id == row.organization_id, User.is_active.is_(True), LearningPerson.position_id == row.id)
    if actor is not None:
        people = people.filter(people_filter(actor))
    employees = people.filter(User.organization_role == 'member').count()
    candidates = people.filter(User.organization_role == 'candidate').count()
    courses = db.query(func.count(Course.id)).filter_by(organization_id=row.organization_id, position_id=row.id, is_deleted=False).scalar()
    return {**{k: getattr(row, k) for k in ['id', 'name', 'description', 'responsibilities', 'competencies', 'revision', 'is_archived']},
            'employee_count': employees, 'candidate_count': candidates, 'course_count': courses}


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
    return {'total': query.count(), 'items': [position_out(db, p, user) for p in query.order_by(BusinessPosition.name, BusinessPosition.id).offset(offset).limit(limit)]}


@router.post('/positions', status_code=201)
def create_position(payload: PositionInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    row = BusinessPosition(**payload.model_dump(), name_key=unique_name(db, org.id, payload.name), organization_id=org.id, created_by=user.id)
    db.add(row); db.flush()
    audit(db, user, 'business.position_created', 'position', row.id, organization_id=org.id)
    db.commit()
    return position_out(db, row, user)


@router.get('/positions/{position_id}')
def get_position(position_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    return position_out(db, position_for(db, org.id, position_id, active=False), user)


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
    return position_out(db, row, user)


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
    return position_out(db, row, user)


@router.get('/employees')
def employees(q: str = Query('', max_length=100), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
              role: Literal['manager', 'member', 'candidate'] | None = None, manager_id: int | None = Query(None, gt=0),
              user_id: int | None = Query(None, gt=0),
              db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    query = db.query(User).filter(people_filter(user), User.is_admin.is_(False))
    if user_id is not None:
        query = query.filter(User.id == user_id)
    if role:
        query = query.filter(User.organization_role.in_(['manager', 'admin']) if role == 'manager' else User.organization_role == role)
    if manager_id is not None:
        query = query.filter(User.manager_id == manager_id)
    if q.strip():
        query = query.filter(User.email.icontains(q.strip(), autoescape=True) | User.username.icontains(q.strip(), autoescape=True) | User.full_name.icontains(q.strip(), autoescape=True))
    from app.routes.administration import user_dict
    rows = query.order_by(User.id).offset(offset).limit(limit).all()
    people = {p.user_id: p for p in db.query(LearningPerson).filter(LearningPerson.organization_id == org.id, LearningPerson.user_id.in_([r.id for r in rows]))}
    return {'total': query.count(), 'items': [user_dict(r) | {'position_id': people[r.id].position_id if r.id in people else None, 'position_name': people[r.id].position if r.id in people else ''} for r in rows]}


@router.get('/dashboard')
def dashboard(db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    from app.models.learning import LearningAssignment
    people = db.query(User).filter(people_filter(user), User.is_active.is_(True), User.is_admin.is_(False)).all()
    learners = [p for p in people if p.organization_role in LEARNER_ROLES]
    person_ids = {p.id for p in learners}
    assignments = db.query(LearningAssignment).filter_by(organization_id=org.id, state='active').filter(LearningAssignment.user_id.in_(person_ids)).all()
    summaries = [(row, learning.assignment_summary(db, row)) for row in assignments]

    def metrics(ids):
        rows = [summary for row, summary in summaries if row.user_id in ids]
        return {'assigned': len(rows), 'completed': sum(r['status'] == 'completed' for r in rows),
                'in_progress': sum(r['status'] == 'in_progress' for r in rows),
                'pending_reviews': sum(r['pending_reviews'] for r in rows),
                'overdue': sum(r['due_at'] is not None and learning.utc(r['due_at']) < datetime.utcnow() and r['status'] != 'completed' for r in rows)}

    managers = []
    if user.organization_role == 'owner':
        for manager in db.query(User).filter(User.organization_id == org.id, User.organization_role.in_(['manager', 'admin']), User.is_admin.is_(False)).order_by(User.full_name, User.id):
            reports = [p for p in learners if p.manager_id == manager.id]
            managers.append({'id': manager.id, 'full_name': manager.full_name, 'email': manager.email, 'is_active': manager.is_active,
                'employees': sum(p.organization_role == 'member' for p in reports), 'candidates': sum(p.organization_role == 'candidate' for p in reports),
                **metrics({p.id for p in reports})})
    return {'scope': 'company' if user.organization_role == 'owner' else 'team', 'company_name': org.name,
            'employees': sum(p.organization_role == 'member' for p in learners),
            'candidates': sum(p.organization_role == 'candidate' for p in learners),
            'unassigned': sum(p.manager_id is None for p in learners),
            'managers': managers, 'metrics': metrics(person_ids)}


@router.put('/employees/{user_id}/reporting-line')
def reporting_line(user_id: int, payload: ReportingLine, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ids = [user.id, user_id] + ([payload.manager_id] if payload.manager_id else [])
    identities = lock_members(db, ids, allow_inactive=True, expected_auth_versions={user.id: user.auth_version})
    actor, target = identities[user.id], identities[user_id]
    org = require_business(db, actor, owner=True)
    require_person(db, actor, target)
    if target.organization_role not in LEARNER_ROLES:
        raise HTTPException(422, 'Руководитель назначается сотруднику или кандидату')
    if target.manager_id != payload.expected_manager_id or target.auth_version != payload.expected_auth_version:
        raise HTTPException(409, 'Участник уже изменён. Обновите список перед сохранением.')
    target.manager_id = resolve_manager(db, actor, org.id, target.organization_role, payload.manager_id)
    target.auth_version += 1
    audit(db, actor, 'business.reporting_line_changed', 'user', target.id, organization_id=org.id, manager_id=target.manager_id)
    db.commit()
    return {'user_id': target.id, 'manager_id': target.manager_id, 'auth_version': target.auth_version}


@router.put('/employees/{user_id}/role')
def learner_role(user_id: int, payload: LearnerRole, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], allow_inactive=True, expected_auth_versions={user.id: user.auth_version})
    actor, target = identities[user.id], identities[user_id]
    org = require_business(db, actor)
    require_person(db, actor, target)
    if target.organization_role not in LEARNER_ROLES:
        raise HTTPException(422, 'Можно изменить только статус сотрудника или кандидата')
    if target.auth_version != payload.expected_auth_version:
        raise HTTPException(409, 'Участник уже изменён. Обновите список перед сохранением.')
    target.organization_role = payload.role
    target.auth_version += 1
    audit(db, actor, 'business.learner_role_changed', 'user', target.id, organization_id=org.id, role=target.organization_role)
    db.commit()
    return {'user_id': target.id, 'role': target.organization_role, 'results_preserved': True}


@router.patch('/employees/{user_id}/profile')
def employee_profile(user_id: int, payload: EmployeeProfile, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], allow_inactive=True,
        expected_auth_versions={user.id: user.auth_version})
    actor, target = identities[user.id], identities[user_id]
    org = require_business(db, actor)
    require_person(db, actor, target)
    if target.id == actor.id or target.organization_role not in {'member', 'candidate', 'manager', 'admin'}:
        raise HTTPException(404, 'Участник не найден в вашей команде')
    if target.full_name != payload.expected_full_name:
        raise HTTPException(409, 'Имя участника уже изменено. Обновите карточку; введённое имя сохранено в форме.')
    target.full_name = payload.full_name
    audit(db, actor, 'business.employee_profile_updated', 'user', target.id, organization_id=org.id)
    db.commit()
    return {'user_id': target.id, 'full_name': target.full_name}


@router.put('/employees/{user_id}/position')
def assign_position(user_id: int, payload: EmployeePosition, db: Session = Depends(get_db), user=Depends(get_current_user)):
    identities = lock_members(db, [user.id, user_id], expected_auth_versions={user.id: user.auth_version})
    org = require_business(db, identities[user.id])
    target = identities[user_id]
    require_person(db, user, target)
    if target.organization_role not in LEARNER_ROLES:
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
    if target.organization_id != org.id or target.is_admin or target.organization_role not in {'member', 'candidate', 'manager', 'admin'}:
        raise HTTPException(404, 'Сотрудник или управляющий не найден')
    if target.auth_version != payload.expected_auth_version:
        raise HTTPException(409, 'Доступ сотрудника уже изменён. Обновите список.')
    if target.organization_role in {'manager', 'admin'} and payload.role == 'member':
        clear_reporting_lines(db, target)
    target.organization_role = payload.role
    if payload.role == 'manager':
        target.manager_id = None
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
    require_person(db, actor, target)
    if target.id == actor.id or target.organization_role not in {'member', 'candidate', 'manager', 'admin'}:
        raise HTTPException(404, 'Сотрудник или управляющий не найден')
    if target.organization_role in {'manager', 'admin'} and actor.organization_role != 'owner':
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
    return make_invitation(db, user, InviteInput(email=payload.email, organization_id=org.id, role=payload.role, manager_id=payload.manager_id, locale=payload.locale), position_id=payload.position_id)


@router.get('/invitations')
def invitations(db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user)
    from app.routes.administration import invitation_dict
    query = db.query(Invitation).filter_by(organization_id=org.id, accepted_at=None, revoked_at=None).filter(Invitation.expires_at > datetime.utcnow())
    if user.organization_role != 'owner':
        query = query.filter(Invitation.manager_id == user.id, Invitation.role.in_(LEARNER_ROLES))
    return {'items': [invitation_dict(i) | {'position_id': i.position_id} for i in query.order_by(Invitation.id.desc()).limit(100)]}


@router.delete('/invitations/{invitation_id}')
def revoke_invitation(invitation_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    org = require_business(db, user, lock=True)
    row = db.query(Invitation).filter_by(id=invitation_id, organization_id=org.id, accepted_at=None, revoked_at=None).first()
    if not row or (user.organization_role != 'owner' and (row.role not in LEARNER_ROLES or row.manager_id != user.id)):
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
        people_filter(user), User.is_active.is_(True), User.organization_role.in_(LEARNER_ROLES), LearningPerson.position_id == position.id).all()
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
