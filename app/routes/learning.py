"""Organization administration and immutable, tenant-scoped learner APIs."""
from copy import deepcopy
from datetime import datetime, timedelta
import csv
import io
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.administration import AdminEvent, Organization
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.user import User
from app.models.learning import *
from app.schemas.learning import *
from app.services.auth_service import get_current_user, require_admin
from app.services.organization_access import audit, lock_org
from app.services import learning_service as svc

router = APIRouter()


def settings_out(row):
    return {k: getattr(row, k) for k in ['organization_id', 'revision', 'profile', 'languages', 'role_labels', 'features', 'delivery', 'rules', 'ai_context', 'updated_at']}


@router.get('/team/permissions')
def permissions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    grants = svc.role_grants(db, user) if user.organization_id else []
    return {'organization_id': user.organization_id, 'role': user.organization_role, 'is_operator': user.is_admin,
            'can_manage_team': bool(user.organization_id and svc.is_manager(db, user)),
            'can_manage_admins': user.organization_role == 'owner',
            'can_author': not user.organization_id or user.organization_role == 'author' or any(g.role == 'author' for g in grants),
            'can_review': any(g.role == 'reviewer' for g in grants),
            'can_report': bool(user.organization_id and (svc.is_manager(db, user) or any(g.role in {'manager', 'reviewer'} for g in grants))),
            'grants': [{k: getattr(g, k) for k in ['id', 'role', 'scope_type', 'scope_id']} for g in grants]}


@router.get('/team/settings')
def team_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.settings_for(db, svc.organization(user))
    result = settings_out(row)
    if user.organization_role != 'owner':
        result['delivery'] = {k: v for k, v in row.delivery.items() if k not in {'commercial_terms', 'billing'}}
    return result


@router.put('/team/settings')
def update_settings(payload: SettingsInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    row = svc.settings_for(db, org_id, True)
    if row.revision != payload.expected_revision:
        raise HTTPException(409, 'Настройки изменились. Обновите данные и повторите изменение.')
    if payload.rules.allow_ai_grading and not row.features.get('ai_grading', False):
        raise HTTPException(403, 'AI-проверка не включена в поставку организации')
    if payload.rules.allow_ai_assistant and row.features.get('assistant') is False:
        raise HTTPException(403, 'Помощник не включён в поставку организации')
    for key, value in payload.model_dump(exclude={'expected_revision'}).items():
        setattr(row, key, value)
    row.revision += 1
    audit(db, user, 'learning.settings_changed', 'organization', org_id, organization_id=org_id, revision=row.revision)
    db.commit()
    return settings_out(row)


@router.get('/admin/organizations/{org_id}/delivery')
def delivery(org_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    lock_org(db, org_id)
    return settings_out(svc.settings_for(db, org_id))


@router.put('/admin/organizations/{org_id}/delivery')
def update_delivery(org_id: int, payload: DeliveryInput, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    lock_org(db, org_id)
    row = svc.settings_for(db, org_id, True)
    if row.revision != payload.expected_revision:
        raise HTTPException(409, 'Настройки изменились')
    if len(json.dumps(payload.delivery, ensure_ascii=False)) > 30000:
        raise HTTPException(422, 'Слишком большой набор настроек поставки')
    row.features, row.delivery = payload.features, payload.delivery
    for key in ['profile', 'languages', 'role_labels', 'ai_context']:
        value = getattr(payload, key)
        if value is not None:
            setattr(row, key, value)
    row.revision += 1
    audit(db, user, 'organization.delivery_changed', 'organization', org_id, organization_id=org_id, revision=row.revision)
    db.commit()
    return settings_out(row)


def group_out(db, row):
    return {'id': row.id, 'name': row.name, 'description': row.description, 'is_archived': row.is_archived,
            'user_ids': [x.user_id for x in db.query(LearningGroupMember).filter_by(group_id=row.id)],
            'staff': [{k: getattr(x, k) for k in ['id', 'user_id', 'role']} for x in db.query(LearningRoleGrant).filter_by(organization_id=row.organization_id, scope_type='group', scope_id=row.id)]}


@router.get('/team/groups')
def groups(q: str = Query('', max_length=100), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    query = db.query(LearningGroup).filter_by(organization_id=org_id, is_archived=False)
    if q:
        query = query.filter(LearningGroup.name.icontains(q, autoescape=True))
    rows = query.order_by(LearningGroup.name, LearningGroup.id).all()
    if not svc.is_manager(db, user):
        scoped_ids = {g.scope_id for g in svc.role_grants(db, user) if g.scope_type == 'group'}
        own_ids = {g.group_id for g in db.query(LearningGroupMember).filter_by(user_id=user.id)}
        rows = [r for r in rows if r.id in scoped_ids | own_ids]
    return {'items': [group_out(db, r) for r in rows]}


@router.post('/team/groups', status_code=201)
def create_group(payload: GroupInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = LearningGroup(organization_id=svc.require_manager(db, user), **payload.model_dump())
    db.add(row)
    db.flush()
    audit(db, user, 'group.created', 'group', row.id, organization_id=row.organization_id)
    db.commit()
    return group_out(db, row)


@router.put('/team/groups/{group_id}')
def update_group(group_id: int, payload: GroupInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.group(db, svc.require_manager(db, user), group_id)
    row.name, row.description = payload.name, payload.description
    audit(db, user, 'group.updated', 'group', row.id, organization_id=row.organization_id)
    db.commit()
    return group_out(db, row)


@router.delete('/team/groups/{group_id}')
def archive_group(group_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.group(db, svc.require_manager(db, user), group_id)
    row.is_archived = True
    db.query(LearningGroupEnrollment).filter_by(group_id=row.id).update({'is_active': False})
    audit(db, user, 'group.archived', 'group', row.id, organization_id=row.organization_id)
    db.commit()
    return {'ok': True, 'assignments_preserved': True}


@router.post('/team/groups/{group_id}/members')
def add_group_members(group_id: int, payload: MemberIds, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    lock_org(db, org_id)
    row = svc.group(db, org_id, group_id)
    targets = [svc.member(db, org_id, uid) for uid in set(payload.user_ids)]
    enrollments = db.query(LearningGroupEnrollment).filter_by(group_id=row.id, is_active=True, include_future_members=True).all()
    for target in targets:
        if not db.get(LearningGroupMember, (row.id, target.id)):
            db.add(LearningGroupMember(group_id=row.id, user_id=target.id))
            for e in enrollments:
                svc.create_assignment(db, org_id=org_id, version_id=e.course_version_id, user_id=target.id, actor_id=e.assigned_by,
                    rules=e.rules, rules_revision=e.rules_revision, due_at=e.due_at, group_id=row.id, cycle_key=e.cycle_key)
    audit(db, user, 'group.members_added', 'group', row.id, organization_id=org_id, user_ids=sorted(set(payload.user_ids)))
    db.commit()
    return group_out(db, row)


@router.delete('/team/groups/{group_id}/members/{user_id}')
def remove_group_member(group_id: int, user_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    row = svc.group(db, org_id, group_id)
    membership = db.get(LearningGroupMember, (row.id, user_id))
    if membership:
        db.delete(membership)
        audit(db, user, 'group.member_removed', 'group', row.id, organization_id=org_id, user_id=user_id)
    db.commit()
    return {'ok': True, 'assignments_preserved': True}


@router.get('/team/departments')
def departments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    rows = db.query(LearningDepartment).filter_by(organization_id=org_id, is_archived=False).order_by(LearningDepartment.name)
    return {'items': [{k: getattr(r, k) for k in ['id', 'name', 'parent_id']} for r in rows]}


@router.post('/team/departments', status_code=201)
def create_department(payload: DepartmentInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    if payload.parent_id and not db.query(LearningDepartment).filter_by(id=payload.parent_id, organization_id=org_id, is_archived=False).first():
        raise HTTPException(404, 'Родительское подразделение не найдено')
    row = LearningDepartment(organization_id=org_id, **payload.model_dump())
    db.add(row)
    db.flush()
    audit(db, user, 'department.created', 'department', row.id, organization_id=org_id)
    db.commit()
    return {'id': row.id, 'name': row.name, 'parent_id': row.parent_id}


@router.put('/team/departments/{department_id}')
def update_department(department_id: int, payload: DepartmentInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    row = db.query(LearningDepartment).filter_by(id=department_id, organization_id=org_id, is_archived=False).first()
    if not row:
        raise HTTPException(404, 'Подразделение не найдено')
    parent_id = payload.parent_id
    seen = {row.id}
    while parent_id:
        if parent_id in seen:
            raise HTTPException(422, 'Подразделения не могут образовывать цикл')
        seen.add(parent_id)
        parent = db.query(LearningDepartment).filter_by(id=parent_id, organization_id=org_id, is_archived=False).first()
        if not parent:
            raise HTTPException(404, 'Родительское подразделение не найдено')
        parent_id = parent.parent_id
    row.name, row.parent_id = payload.name, payload.parent_id
    audit(db, user, 'department.updated', 'department', row.id, organization_id=org_id)
    db.commit()
    return {'id': row.id, 'name': row.name, 'parent_id': row.parent_id}


@router.delete('/team/departments/{department_id}')
def archive_department(department_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    row = db.query(LearningDepartment).filter_by(id=department_id, organization_id=org_id).first()
    if not row:
        raise HTTPException(404, 'Подразделение не найдено')
    if db.query(LearningPerson).filter_by(department_id=row.id).count() or db.query(LearningDepartment).filter_by(parent_id=row.id, is_archived=False).count():
        raise HTTPException(409, 'Сначала переместите сотрудников и дочерние подразделения')
    row.is_archived = True
    audit(db, user, 'department.archived', 'department', row.id, organization_id=org_id)
    db.commit()
    return {'ok': True}


@router.get('/team/people')
def people(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    rows = db.query(LearningPerson).filter_by(organization_id=org_id).all()
    if not svc.is_manager(db, user):
        rows = [r for r in rows if r.user_id == user.id or svc.can_scope(db, user, 'manager', user_id=r.user_id)]
    return {'items': [{k: getattr(r, k) for k in ['user_id', 'department_id', 'position', 'external_id', 'attributes']} for r in rows]}


@router.put('/team/people/{user_id}')
def update_person(user_id: int, payload: PersonInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    svc.member(db, org_id, user_id)
    if payload.department_id and not db.query(LearningDepartment).filter_by(id=payload.department_id, organization_id=org_id, is_archived=False).first():
        raise HTTPException(404, 'Подразделение не найдено')
    if len(json.dumps(payload.attributes, ensure_ascii=False)) > 10000:
        raise HTTPException(422, 'Слишком большой набор атрибутов')
    row = db.get(LearningPerson, user_id)
    if row is None:
        row = LearningPerson(user_id=user_id, organization_id=org_id)
        db.add(row)
    row.organization_id = org_id
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    audit(db, user, 'person.updated', 'user', user_id, organization_id=org_id)
    db.commit()
    return {'user_id': user_id, **payload.model_dump()}


@router.get('/team/members/{user_id}/roles')
def get_roles(user_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    target = svc.member(db, org_id, user_id, active=False)
    return {'role': target.organization_role, 'items': [{k: getattr(g, k) for k in ['id', 'role', 'scope_type', 'scope_id']} for g in svc.role_grants(db, target)]}


@router.post('/team/members/{user_id}/roles', status_code=201)
def grant_role(user_id: int, payload: RoleInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    lock_org(db, org_id)
    target = svc.member(db, org_id, user_id)
    if target.id == user.id or target.is_admin:
        raise HTTPException(403, 'Нельзя изменять собственные права или права оператора')
    if payload.role == 'admin' and user.organization_role != 'owner':
        raise HTTPException(403, 'Только владелец назначает администратора')
    if payload.role in {'reviewer', 'manager'} and payload.scope_type == 'organization':
        raise HTTPException(422, 'Проверяющему и руководителю нужна явная область: курс, группа или подразделение')
    if payload.role in {'admin', 'member'} and payload.scope_type != 'organization':
        raise HTTPException(422, 'Эта роль задаётся на уровне организации')
    if payload.scope_type == 'organization' and payload.scope_id != 0:
        raise HTTPException(422, 'Область организации имеет scope_id=0')
    if payload.scope_type == 'course':
        svc.org_course(db, user, payload.scope_id)
    elif payload.scope_type == 'group':
        svc.group(db, org_id, payload.scope_id)
    elif payload.scope_type == 'department':
        if not db.query(LearningDepartment).filter_by(id=payload.scope_id, organization_id=org_id, is_archived=False).first():
            raise HTTPException(404, 'Подразделение не найдено')
    row = db.query(LearningRoleGrant).filter_by(organization_id=org_id, user_id=user_id, **payload.model_dump()).first()
    if row is None:
        row = LearningRoleGrant(organization_id=org_id, user_id=user_id, created_by=user.id, **payload.model_dump())
        db.add(row)
        db.flush()
        audit(db, user, 'role.granted', 'user', user_id, organization_id=org_id, **payload.model_dump())
    db.commit()
    return {k: getattr(row, k) for k in ['id', 'role', 'scope_type', 'scope_id']}


@router.delete('/team/members/{user_id}/roles/{grant_id}')
def revoke_role(user_id: int, grant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    target = svc.member(db, org_id, user_id, active=False)
    row = db.query(LearningRoleGrant).filter_by(id=grant_id, user_id=user_id, organization_id=org_id).first()
    if not row:
        raise HTTPException(404, 'Назначение роли не найдено')
    if user_id == user.id or (row.role == 'admin' and user.organization_role != 'owner'):
        raise HTTPException(403, 'Недостаточно прав для отзыва роли')
    audit(db, user, 'role.revoked', 'user', user_id, organization_id=org_id, role=row.role, scope_type=row.scope_type, scope_id=row.scope_id)
    db.delete(row)
    target.auth_version += 1
    db.commit()
    return {'ok': True}


@router.post('/team/ownership-transfers', status_code=201)
def start_transfer(payload: OwnerTransferInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    lock_org(db, org_id)
    if user.organization_role != 'owner' or payload.user_id == user.id:
        raise HTTPException(403, 'Передать владение может действующий владелец другому участнику')
    target = svc.member(db, org_id, payload.user_id)
    if target.is_admin:
        raise HTTPException(403, 'Оператор не может стать владельцем организации через передачу')
    db.query(OrganizationOwnershipTransfer).filter_by(organization_id=org_id, from_user_id=user.id, accepted_at=None, cancelled_at=None).update({'cancelled_at': datetime.utcnow()})
    row = OrganizationOwnershipTransfer(organization_id=org_id, from_user_id=user.id, to_user_id=target.id, expires_at=datetime.utcnow() + timedelta(days=7))
    db.add(row)
    db.flush()
    audit(db, user, 'ownership.transfer_requested', 'organization', org_id, organization_id=org_id, to_user_id=target.id)
    db.commit()
    return {'id': row.id, 'to_user_id': target.id, 'expires_at': row.expires_at}


@router.get('/team/ownership-transfers')
def transfers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    rows = db.query(OrganizationOwnershipTransfer).filter_by(organization_id=org_id, accepted_at=None, cancelled_at=None).filter(OrganizationOwnershipTransfer.expires_at > datetime.utcnow(), (OrganizationOwnershipTransfer.from_user_id == user.id) | (OrganizationOwnershipTransfer.to_user_id == user.id))
    return {'items': [{k: getattr(r, k) for k in ['id', 'from_user_id', 'to_user_id', 'expires_at']} for r in rows]}


@router.post('/team/ownership-transfers/{transfer_id}/accept')
def accept_transfer(transfer_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    lock_org(db, org_id)
    row = db.query(OrganizationOwnershipTransfer).filter_by(id=transfer_id, organization_id=org_id, to_user_id=user.id).first()
    if not row or row.accepted_at or row.cancelled_at or row.expires_at <= datetime.utcnow():
        raise HTTPException(410, 'Предложение передачи уже использовано, отменено или истекло')
    old = svc.member(db, org_id, row.from_user_id)
    if old.organization_role != 'owner':
        raise HTTPException(409, 'Инициатор больше не владелец организации')
    user.organization_role, old.organization_role = 'owner', 'admin'
    # Both sessions are revoked; the new owner signs in with their own password.
    user.auth_version += 1
    old.auth_version += 1
    row.accepted_at = datetime.utcnow()
    audit(db, user, 'ownership.transferred', 'organization', org_id, organization_id=org_id, from_user_id=old.id, to_user_id=user.id)
    db.commit()
    return {'ok': True, 'reauthentication_required': True}


@router.delete('/team/ownership-transfers/{transfer_id}')
def cancel_transfer(transfer_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    row = db.query(OrganizationOwnershipTransfer).filter_by(id=transfer_id, organization_id=org_id).first()
    if not row or user.id not in {row.from_user_id, row.to_user_id}:
        raise HTTPException(404, 'Предложение не найдено')
    if row.accepted_at:
        raise HTTPException(409, 'Передача уже завершена')
    row.cancelled_at = datetime.utcnow()
    audit(db, user, 'ownership.transfer_cancelled', 'organization', org_id, organization_id=org_id)
    db.commit()
    return {'ok': True}


def assessment_settings(kind, item_id, payload, db, user):
    from app.services.course_editor_service import CourseEditorService
    from app.models.task import Task
    from app.services.course_publication_service import CoursePublicationService
    if kind == 'test':
        item = CourseEditorService._test(db, item_id, user.id, True)
        course = item.module.course if item.module_id else db.get(Course, item.course_id)
        answers = json.loads(item.answers or '[]')
        if payload.question_type != 'open' and any(a not in answers for a in payload.correct_answers):
            raise HTTPException(422, 'Правильные ответы должны входить в варианты задания')
    else:
        item = db.get(Task, item_id)
        if not item or item.is_deleted:
            raise HTTPException(404, 'Практическое задание не найдено')
        CourseEditorService._module(db, item.module_id, user.id, True)
        course = item.module.course
        if payload.question_type != 'open':
            raise HTTPException(422, 'Практическое задание использует открытый ответ')
    row = db.query(AssessmentDefinition).filter(getattr(AssessmentDefinition, kind + '_id') == item_id).first()
    if (row.revision if row else 0) != payload.expected_revision:
        raise HTTPException(409, 'Настройки задания изменены другим редактором')
    if row is None:
        row = AssessmentDefinition(**{kind + '_id': item_id}, revision=1)
        db.add(row)
    else:
        row.revision += 1
    for key, value in payload.model_dump(exclude={'expected_revision'}).items():
        setattr(row, key, value)
    CoursePublicationService.prepare_for_edit(course)
    audit(db, user, 'assessment.settings_changed', kind, item_id, organization_id=svc.course_org(db, course), revision=row.revision)
    db.commit()
    return {k: getattr(row, k) for k in ['question_type', 'correct_answers', 'max_points', 'criteria', 'grading_mode', 'revision']}


@router.put('/tests/{test_id}/assessment-settings')
def test_assessment(test_id: int, payload: AssessmentInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return assessment_settings('test', test_id, payload, db, user)


@router.put('/tasks/{task_id}/assessment-settings')
def task_assessment(task_id: int, payload: AssessmentInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return assessment_settings('task', task_id, payload, db, user)


def read_assessment_settings(db, user, kind, item_id):
    from app.services.course_editor_service import CourseEditorService
    from app.models.task import Task
    if kind == 'test':
        item = CourseEditorService._test(db, item_id, user.id)
    else:
        item = db.get(Task, item_id)
        if not item or item.is_deleted:
            raise HTTPException(404, 'Практическое задание не найдено')
        CourseEditorService._module(db, item.module_id, user.id)
    definition = db.query(AssessmentDefinition).filter(getattr(AssessmentDefinition, kind + '_id') == item.id).first()
    if definition:
        return {k: getattr(definition, k) for k in ['question_type', 'correct_answers', 'max_points', 'criteria', 'grading_mode', 'revision']}
    data = svc._assessment(db, item, kind)
    return {**{k: data[k] for k in ['question_type', 'correct_answers', 'max_points', 'criteria', 'grading_mode']}, 'revision': 0}


@router.get('/tests/{test_id}/assessment-settings')
def get_test_assessment(test_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return read_assessment_settings(db, user, 'test', test_id)


@router.get('/tasks/{task_id}/assessment-settings')
def get_task_assessment(task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return read_assessment_settings(db, user, 'task', task_id)


@router.get('/team/course-directory')
def course_directory(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Management metadata, including drafts needing their first reviewer grant."""
    org_id = svc.require_manager(db, user)
    rows = db.query(Course).filter_by(organization_id=org_id, is_deleted=False).order_by(Course.name, Course.id).all()
    return {'items': [{'id': r.id, 'title': r.name, 'status': r.status} for r in rows]}


@router.get('/team/catalogue')
def catalogue(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    versions = db.query(CourseVersion).join(Course).join(PublishedLearningContent, PublishedLearningContent.course_version_id == CourseVersion.id).filter(Course.organization_id == org_id, Course.is_deleted.is_(False), CourseVersion.publication_status == 'published').order_by(CourseVersion.course_id, CourseVersion.revision.desc()).all()
    if not svc.is_manager(db, user):
        allowed = {g.scope_id for g in svc.role_grants(db, user) if g.role == 'manager' and g.scope_type == 'course'}
        versions = [v for v in versions if v.course_id in allowed]
    return {'items': [{'course_id': v.course_id, 'course_version_id': v.id, 'title': v.name, 'revision': v.revision, 'published_at': v.published_at} for v in versions]}


@router.post('/courses/{course_id}/assignments', status_code=201)
def assign_course(course_id: int, payload: AssignmentInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    lock_org(db, org_id)  # serializes duplicate clicks and group enrollment changes
    course = svc.org_course(db, user, course_id)
    if course.is_deleted:
        raise HTTPException(409, 'Архивный курс нельзя назначить заново')
    version_query = db.query(CourseVersion).filter_by(course_id=course.id, publication_status='published')
    if payload.course_version_id:
        version_query = version_query.filter_by(id=payload.course_version_id)
    version = version_query.order_by(CourseVersion.revision.desc()).first()
    if not version:
        raise HTTPException(409, 'Сначала опубликуйте курс')
    svc.snapshot_for(db, version.id)
    config = svc.settings_for(db, org_id)
    if config.features.get('learning') is False:
        raise HTTPException(403, 'Обучение не включено в поставку организации')
    rules = LearningRules.model_validate(config.rules).model_dump()
    if set(payload.rule_overrides) - set(rules['assignment_overrides']):
        raise HTTPException(403, 'Переопределение этих правил запрещено настройками организации')
    rules = LearningRules.model_validate({**rules, **payload.rule_overrides}).model_dump()
    target_groups = [svc.group(db, org_id, gid) for gid in sorted(set(payload.group_ids))]
    targets = {uid: None for uid in set(payload.user_ids)}
    for g in target_groups:
        svc.allowed_assigner(db, user, course, group_id=g.id)
        for membership in db.query(LearningGroupMember).filter_by(group_id=g.id):
            target = db.get(User, membership.user_id)
            if target and target.organization_id == org_id and target.is_active:
                targets.setdefault(target.id, g.id)
    # Validate every target before making any records.
    for uid, gid in targets.items():
        svc.member(db, org_id, uid)
        svc.allowed_assigner(db, user, course, target_id=uid, group_id=gid)
    rows, created_count = [], 0
    for uid, gid in targets.items():
        row, created = svc.create_assignment(db, org_id=org_id, version_id=version.id, user_id=uid, actor_id=user.id,
            rules=rules, rules_revision=config.revision, due_at=payload.due_at, group_id=gid, cycle_key=payload.cycle_key)
        rows.append(row)
        created_count += int(created)
        if created:
            audit(db, user, 'learning.assigned', 'assignment', row.id, organization_id=org_id, user_id=uid, course_version_id=version.id)
    for g in target_groups:
        existing = db.query(LearningGroupEnrollment).filter_by(group_id=g.id, course_version_id=version.id, cycle_key=payload.cycle_key).first()
        if not existing:
            db.add(LearningGroupEnrollment(organization_id=org_id, group_id=g.id, course_version_id=version.id, cycle_key=payload.cycle_key,
                assigned_by=user.id, rules=rules, rules_revision=config.revision, due_at=svc.utc(payload.due_at), include_future_members=payload.include_future_members))
    db.commit()
    return {'created': created_count, 'items': [svc.assignment_summary(db, r) for r in rows]}


@router.get('/courses/{course_id}/assignments')
def course_assignments(course_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    course = svc.org_course(db, user, course_id)
    rows = db.query(LearningAssignment).join(CourseVersion).filter(LearningAssignment.organization_id == user.organization_id, CourseVersion.course_id == course.id).order_by(LearningAssignment.id.desc()).all()
    rows = [r for r in rows if svc.can_report(db, user, r)]
    return {'items': [svc.assignment_summary(db, r) for r in rows]}


@router.delete('/team/group-enrollments/{enrollment_id}')
def revoke_group_enrollment(enrollment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    row = db.query(LearningGroupEnrollment).filter_by(id=enrollment_id, organization_id=org_id).first()
    if not row:
        raise HTTPException(404, 'Назначение группы не найдено')
    row.is_active = False
    audit(db, user, 'learning.group_future_enrollment_stopped', 'group', row.group_id, organization_id=org_id)
    db.commit()
    return {'ok': True, 'existing_assignments_preserved': True}


@router.delete('/learning/assignments/{assignment_id}')
def revoke_assignment(assignment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    lock_org(db, org_id)
    row = db.query(LearningAssignment).filter_by(id=assignment_id, organization_id=org_id).first()
    if not row:
        raise HTTPException(404, 'Назначение не найдено')
    version = db.get(CourseVersion, row.course_version_id)
    svc.allowed_assigner(db, user, svc.org_course(db, user, version.course_id), target_id=row.user_id, group_id=row.group_id)
    if row.state != 'revoked':
        row.state, row.revoked_at = 'revoked', datetime.utcnow()
        audit(db, user, 'learning.assignment_revoked', 'assignment', row.id, organization_id=org_id)
        svc.event(db, org_id, 'assignment.revoked', f'assignment:{row.id}:revoked', {'assignment_id': row.id})
    db.commit()
    return {'ok': True, 'results_preserved': True}


@router.get('/learning/assignments')
def my_learning(include_history: bool = False, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not user.organization_id:
        return {'total': 0, 'items': []}
    org_id = svc.organization(user)
    query = db.query(LearningAssignment).filter_by(user_id=user.id, organization_id=org_id)
    if not include_history:
        query = query.filter_by(state='active')
    total = query.count()
    rows = query.order_by(LearningAssignment.id.desc()).offset(offset).limit(limit)
    return {'total': total, 'items': [svc.assignment_summary(db, r) for r in rows]}


@router.get('/learning/assignments/{assignment_id}')
def my_assignment(assignment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.learner_assignment(db, user, assignment_id)
    responses = db.query(LearningResponse).filter_by(assignment_id=row.id).order_by(LearningResponse.assessment_id, LearningResponse.attempt)
    return {**svc.assignment_summary(db, row), 'content': svc.public_snapshot(db, row), 'responses': [svc.response_dict(db, r, row) for r in responses]}


@router.put('/learning/assignments/{assignment_id}/progress')
def progress(assignment_id: int, payload: ProgressInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.learner_assignment(db, user, assignment_id, lock=True)
    svc.ensure_learning_write(db, row.organization_id)
    snapshot = svc.snapshot_for(db, row.course_version_id)
    lesson_ids = {l['id'] for m in snapshot['modules'] for l in m['lessons']}
    if payload.lesson_id not in lesson_ids:
        raise HTTPException(404, 'Урок не найден в назначенной версии')
    if row.progress_revision != payload.expected_revision:
        raise HTTPException(409, {'message': 'Прогресс изменился на другом устройстве. Обновите данные.', 'current_revision': row.progress_revision})
    completed = set(row.completed_lessons)
    if payload.completed:
        completed.add(payload.lesson_id)
    # Completion is monotonic; opening an earlier lesson never erases progress.
    row.completed_lessons = sorted(completed)
    row.last_lesson_id = payload.lesson_id
    row.progress_revision += 1
    db.flush()
    svc.update_completion(db, row)
    db.commit()
    return svc.assignment_summary(db, row)


@router.put('/learning/assignments/{assignment_id}/responses/{assessment_id}')
def save_response(assignment_id: int, assessment_id: str, payload: AnswerInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.learner_assignment(db, user, assignment_id, lock=True)
    response = svc.save_answer(db, user, row, assessment_id, payload)
    db.commit()
    return svc.response_dict(db, response, row)


@router.post('/learning/assignments/{assignment_id}/responses/{assessment_id}/submit')
def submit_response(assignment_id: int, assessment_id: str, payload: SubmitInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.learner_assignment(db, user, assignment_id, lock=True)
    response = svc.save_answer(db, user, row, assessment_id, payload, submit=True)
    db.commit()
    return svc.response_dict(db, response, row)


@router.get('/learning/assignments/{assignment_id}/results')
def my_results(assignment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.learner_assignment(db, user, assignment_id, allow_revoked=True, allow_locked=True)
    responses = db.query(LearningResponse).filter_by(assignment_id=row.id).order_by(LearningResponse.assessment_id, LearningResponse.attempt)
    return {**svc.assignment_summary(db, row), 'responses': [svc.response_dict(db, r, row) for r in responses]}


@router.get('/learning/reviews')
def review_queue(status: Literal['pending_review', 'reviewed'] = 'pending_review', db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    rows = db.query(LearningResponse, LearningAssignment).join(LearningAssignment).filter(LearningAssignment.organization_id == org_id, LearningResponse.status == status).order_by(LearningResponse.submitted_at, LearningResponse.id).all()
    items = []
    for response, assignment in rows:
        if svc.can_review(db, user, assignment):
            person = db.get(User, assignment.user_id)
            item = svc.response_dict(db, response, assignment, include_key=True)
            item.update(user_id=assignment.user_id, full_name=person.full_name, course_title=db.get(CourseVersion, assignment.course_version_id).name, assessment=svc.assessment_for(db, assignment, response.assessment_id))
            items.append(item)
    return {'items': items}


@router.get('/learning/reviews/{response_id}')
def review_detail(response_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    response = db.get(LearningResponse, response_id)
    assignment = db.get(LearningAssignment, response.assignment_id) if response else None
    if not assignment or not svc.can_review(db, user, assignment):
        raise HTTPException(404, 'Работа не найдена в вашей области проверки')
    history = db.query(LearningGradeEvent).filter_by(response_id=response.id).order_by(LearningGradeEvent.id)
    return {**svc.response_dict(db, response, assignment, include_key=True), 'assessment': svc.assessment_for(db, assignment, response.assessment_id),
            'history': [{k: getattr(e, k) for k in ['id', 'actor_id', 'old_points', 'points', 'feedback', 'criteria_scores', 'source', 'created_at']} for e in history]}


@router.post('/learning/reviews/{response_id}/grade')
def grade(response_id: int, payload: GradeInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    response = db.get(LearningResponse, response_id)
    assignment = db.get(LearningAssignment, response.assignment_id) if response else None
    if not assignment or not svc.can_review(db, user, assignment):
        raise HTTPException(404, 'Работа не найдена в вашей области проверки')
    svc.ensure_learning_write(db, assignment.organization_id)
    assignment = db.execute(select(LearningAssignment).where(LearningAssignment.id == assignment.id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    response = db.execute(select(LearningResponse).where(LearningResponse.id == response_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    if assignment.user_id == user.id:
        raise HTTPException(403, 'Нельзя оценить собственную работу')
    if response.status not in {'pending_review', 'reviewed'}:
        raise HTTPException(409, 'Участник ещё не отправил ответ')
    if response.revision != payload.expected_revision:
        raise HTTPException(409, 'Оценка изменена другим проверяющим. Обновите данные.')
    assessment = svc.assessment_for(db, assignment, response.assessment_id)
    if payload.points > assessment['max_points']:
        raise HTTPException(422, 'Балл превышает максимум задания')
    criteria = {c['id']: c for c in assessment['criteria'] if 'id' in c and 'max_points' in c}
    scores = {s.id: s.points for s in payload.criteria_scores}
    if criteria:
        if len(scores) != len(payload.criteria_scores) or set(scores) != set(criteria) or any(scores[k] > criteria[k]['max_points'] for k in scores):
            raise HTTPException(422, 'Укажите допустимый балл по каждому критерию')
        if abs(sum(scores.values()) - payload.points) > 0.0001:
            raise HTTPException(422, 'Итоговый балл должен совпадать с суммой критериев')
    elif scores:
        raise HTTPException(422, 'У задания нет отдельных критериев')
    db.add(LearningGradeEvent(response_id=response.id, actor_id=user.id, old_points=response.points, points=payload.points, feedback=payload.feedback, criteria_scores=[s.model_dump() for s in payload.criteria_scores], source='reviewer'))
    response.points, response.feedback, response.status = payload.points, payload.feedback, 'reviewed'
    response.revision += 1
    response.grading_metadata = {'method': 'reviewer'}
    audit(db, user, 'learning.graded', 'response', response.id, organization_id=assignment.organization_id, points=payload.points, assignment_id=assignment.id)
    svc.event(db, assignment.organization_id, 'submission.graded', f'response:{response.id}:revision:{response.revision}', {'response_id': response.id, 'assignment_id': assignment.id, 'points': response.points, 'max_points': assessment['max_points']})
    db.flush()
    svc.update_completion(db, assignment)
    db.commit()
    return svc.response_dict(db, response, assignment, include_key=True)


def report_rows(db, user, course_id=None, group_id=None, user_id=None):
    org_id = svc.organization(user)
    query = db.query(LearningAssignment).filter_by(organization_id=org_id)
    if course_id:
        query = query.join(CourseVersion).filter(CourseVersion.course_id == course_id)
    if group_id:
        svc.group(db, org_id, group_id, active=False)
        member_ids = select(LearningGroupMember.user_id).where(LearningGroupMember.group_id == group_id)
        query = query.filter((LearningAssignment.group_id == group_id) | LearningAssignment.user_id.in_(member_ids))
    if user_id:
        query = query.filter_by(user_id=user_id)
    return [r for r in query.order_by(LearningAssignment.id.desc()) if svc.can_report(db, user, r)]


@router.get('/team/results')
def results(course_id: int | None = None, group_id: int | None = None, user_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = report_rows(db, user, course_id, group_id, user_id)
    items = []
    for row in rows:
        person = db.get(User, row.user_id)
        items.append({**svc.assignment_summary(db, row), 'full_name': person.full_name, 'email': person.email})
    active = [x for x in items if x['state'] == 'active']
    return {'items': items, 'metrics': {
        'assigned': len(active), 'completed': sum(x['status'] == 'completed' for x in active),
        'in_progress': sum(x['status'] == 'in_progress' for x in active),
        'overdue': sum(x['due_at'] is not None and svc.utc(x['due_at']) < datetime.utcnow() and x['status'] != 'completed' for x in active),
        'pending_reviews': sum(x['pending_reviews'] for x in active),
        'average_score_percent': round(sum(x['score_percent'] for x in active if x['score_percent'] is not None) / len([x for x in active if x['score_percent'] is not None]), 2) if any(x['score_percent'] is not None for x in active) else None,
    }}


@router.get('/team/results/export')
def results_export(course_id: int | None = None, group_id: int | None = None, user_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = results(course_id, group_id, user_id, db, user)
    stream = io.StringIO(newline='')
    keys = ['id', 'user_id', 'full_name', 'email', 'title', 'revision', 'status', 'progress_percent', 'points', 'max_points', 'score_percent', 'due_at', 'completed_at']
    writer = csv.DictWriter(stream, fieldnames=keys, extrasaction='ignore')
    writer.writeheader()
    for item in data['items']:
        # Do not let names or titles become spreadsheet formulas on export.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for k, v in item.items()})
    audit(db, user, 'learning.results_exported', 'organization', user.organization_id, organization_id=user.organization_id, count=len(data['items']))
    db.commit()
    return Response('\ufeff' + stream.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="learning-results.csv"'})


@router.get('/team/audit')
def team_audit(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    # Explicit event tenant, not the actor's current tenant after a transfer.
    query = db.query(AdminEvent).filter(AdminEvent.details['organization_id'].as_integer() == org_id)
    return {'total': query.count(), 'items': [{k: getattr(r, k) for k in ['id', 'actor_id', 'action', 'target_type', 'target_id', 'details', 'created_at']} for r in query.order_by(AdminEvent.id.desc()).offset(offset).limit(limit)]}


def program_out(row):
    return {k: getattr(row, k) for k in ['id', 'title', 'description', 'revision', 'steps', 'recurrence_days', 'reminder_days', 'is_archived']}


def validate_steps(db, user, steps):
    for step in steps:
        version = db.get(CourseVersion, step.course_version_id)
        if not version:
            raise HTTPException(404, 'Версия курса не найдена')
        course = svc.org_course(db, user, version.course_id)
        if course.is_deleted:
            raise HTTPException(409, 'Архивный курс нельзя включить в новую программу')
        svc.snapshot_for(db, version.id)


@router.get('/team/programs')
def programs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    return {'items': [program_out(r) for r in db.query(LearningProgram).filter_by(organization_id=org_id, is_archived=False).order_by(LearningProgram.title)]}


@router.post('/team/programs', status_code=201)
def create_program(payload: ProgramInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    if svc.settings_for(db, org_id).features.get('programs') is False:
        raise HTTPException(403, 'Программы не включены в поставку')
    validate_steps(db, user, payload.steps)
    row = LearningProgram(organization_id=org_id, **payload.model_dump(exclude={'expected_revision'}))
    db.add(row)
    db.flush()
    audit(db, user, 'program.created', 'program', row.id, organization_id=org_id)
    db.commit()
    return program_out(row)


@router.put('/team/programs/{program_id}')
def update_program(program_id: int, payload: ProgramInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    lock_org(db, org_id)
    row = db.query(LearningProgram).filter_by(id=program_id, organization_id=org_id, is_archived=False).first()
    if not row:
        raise HTTPException(404, 'Программа не найдена')
    if row.revision != payload.expected_revision:
        raise HTTPException(409, 'Программа изменена другим администратором')
    validate_steps(db, user, payload.steps)
    for key, value in payload.model_dump(exclude={'expected_revision'}).items():
        setattr(row, key, value)
    row.revision += 1
    audit(db, user, 'program.updated', 'program', row.id, organization_id=org_id, revision=row.revision)
    db.commit()
    return program_out(row)


@router.delete('/team/programs/{program_id}')
def archive_program(program_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    row = db.query(LearningProgram).filter_by(id=program_id, organization_id=org_id).first()
    if not row:
        raise HTTPException(404, 'Программа не найдена')
    row.is_archived = True
    audit(db, user, 'program.archived', 'program', row.id, organization_id=org_id)
    db.commit()
    return {'ok': True, 'history_preserved': True}


def enroll_program(db, program, user, target, cycle_key, now):
    existing = db.query(LearningProgramEnrollment).filter_by(program_id=program.id, user_id=target.id, cycle_key=cycle_key).first()
    if existing:
        return existing
    config = svc.settings_for(db, program.organization_id)
    snapshot = program_out(program)
    row = LearningProgramEnrollment(organization_id=program.organization_id, program_id=program.id, program_revision=program.revision,
        user_id=target.id, cycle_key=cycle_key, snapshot=deepcopy(snapshot), next_cycle_at=now + timedelta(days=program.recurrence_days) if program.recurrence_days else None)
    db.add(row)
    db.flush()
    ids, previous_id = [], None
    for step in program.steps:
        assignment, _ = svc.create_assignment(db, org_id=program.organization_id, version_id=step['course_version_id'], user_id=target.id,
            actor_id=user.id, rules=LearningRules.model_validate(config.rules).model_dump(), rules_revision=config.revision,
            due_at=now + timedelta(days=step['due_days']) if step.get('due_days') else None, cycle_key=f'program:{row.id}', prerequisite_id=previous_id)
        ids.append(assignment.id)
        previous_id = assignment.id
    row.assignment_ids = ids
    audit(db, user, 'program.assigned', 'program_enrollment', row.id, organization_id=program.organization_id, user_id=target.id, program_revision=program.revision)
    return row


def enrollment_out(db, row):
    assignments = [db.get(LearningAssignment, aid) for aid in row.assignment_ids]
    items = [svc.assignment_summary(db, a) for a in assignments if a]
    return {'id': row.id, 'program_id': row.program_id, 'program_revision': row.program_revision, 'title': row.snapshot['title'],
            'user_id': row.user_id, 'created_at': row.created_at, 'next_cycle_at': row.next_cycle_at,
            'status': 'completed' if items and all(a['status'] == 'completed' for a in items) else 'in_progress',
            'assignments': items, 'next_assignment_id': next((a['id'] for a in items if a['status'] in {'assigned', 'in_progress'}), None)}


@router.post('/team/programs/{program_id}/enrollments', status_code=201)
def assign_program(program_id: int, payload: ProgramEnrollInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    lock_org(db, org_id)
    program = db.query(LearningProgram).filter_by(id=program_id, organization_id=org_id, is_archived=False).first()
    if not program:
        raise HTTPException(404, 'Программа не найдена')
    targets = [svc.member(db, org_id, uid) for uid in sorted(set(payload.user_ids))]
    rows = [enroll_program(db, program, user, target, payload.cycle_key, datetime.utcnow()) for target in targets]
    db.commit()
    return {'items': [enrollment_out(db, r) for r in rows]}


@router.get('/learning/programs')
def my_programs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not user.organization_id:
        return {'items': []}
    org_id = svc.organization(user)
    rows = db.query(LearningProgramEnrollment).filter_by(organization_id=org_id, user_id=user.id).order_by(LearningProgramEnrollment.id.desc())
    return {'items': [enrollment_out(db, r) for r in rows]}


@router.get('/team/programs/{program_id}/enrollments')
def program_enrollments(program_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    rows = db.query(LearningProgramEnrollment).filter_by(organization_id=org_id, program_id=program_id).order_by(LearningProgramEnrollment.id.desc())
    return {'items': [enrollment_out(db, r) for r in rows]}


@router.get('/team/learning-reminders')
def reminders(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = report_rows(db, user)
    now = datetime.utcnow()
    windows = {}
    for enrollment in db.query(LearningProgramEnrollment).filter_by(organization_id=svc.organization(user)):
        for aid in enrollment.assignment_ids:
            windows[aid] = enrollment.snapshot.get('reminder_days', 3)
    due = [r for r in rows if r.state == 'active' and not r.completed_at and r.due_at and svc.utc(r.due_at) <= now + timedelta(days=windows.get(r.id, 3))]
    return {'items': [svc.assignment_summary(db, r) for r in due]}


@router.post('/team/program-enrollments/{enrollment_id}/next-cycle', status_code=201)
def repeat_program(enrollment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.require_manager(db, user)
    lock_org(db, org_id)
    old = db.query(LearningProgramEnrollment).filter_by(id=enrollment_id, organization_id=org_id).first()
    if not old:
        raise HTTPException(404, 'Назначенная программа не найдена')
    if not old.next_cycle_at or old.next_cycle_at > datetime.utcnow():
        raise HTTPException(409, 'Срок повторного обучения ещё не наступил')
    program = db.get(LearningProgram, old.program_id)
    if program.is_archived:
        raise HTTPException(409, 'Программа архивирована')
    target = svc.member(db, org_id, old.user_id)
    row = enroll_program(db, program, user, target, f'repeat:{old.id}', datetime.utcnow())
    db.commit()
    return enrollment_out(db, row)


@router.get('/learning/course-reviews')
def course_review_queue(after_id: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = svc.organization(user)
    allowed = [g.scope_id for g in svc.role_grants(db, user) if g.role == 'reviewer' and g.scope_type == 'course']
    rows = db.query(Course).filter(Course.organization_id == org_id, Course.is_deleted.is_(False),
        Course.id.in_(allowed), Course.id > after_id, Course.owner_id != user.id).order_by(Course.id).limit(limit + 1).all()
    return {'items': [{'id': row.id, 'title': row.name, 'revision': row.content_revision, 'status': row.status} for row in rows[:limit]],
            'next_cursor': rows[limit-1].id if len(rows) > limit else None}


@router.get('/courses/{course_id}/approvals')
def publication_approvals(course_id: int, before_id: int | None = Query(default=None, gt=0),
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    course = svc.org_course(db, user, course_id)
    if course.is_deleted or not (course.owner_id == user.id or svc.can_scope(db, user, 'author', course_id=course.id)
                                or svc.can_scope(db, user, 'reviewer', course_id=course.id)):
        raise HTTPException(404, 'Курс не найден')
    modules = sorted((item for item in course.modules if not item.is_deleted), key=lambda item: (item.position, item.id))
    digest = svc.content_digest(svc.build_publication_snapshot(db, course, modules))
    query = db.query(LearningPublicationApproval).filter_by(course_id=course.id, organization_id=course.organization_id)
    if before_id:
        query = query.filter(LearningPublicationApproval.id < before_id)
    rows = query.order_by(LearningPublicationApproval.id.desc()).limit(51).all()
    return {'required': bool(svc.settings_for(db, course.organization_id).rules.get('require_independent_approval')),
            'content_digest': digest, 'items': [{k: getattr(row, k) for k in
                ['id', 'reviewer_id', 'decision', 'revision', 'content_digest', 'comment', 'created_at']} for row in rows[:50]],
            'next_cursor': rows[49].id if len(rows) > 50 else None}


@router.get('/courses/{course_id}/review-content')
def review_content(course_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    course = svc.org_course(db, user, course_id)
    if course.is_deleted or not svc.can_scope(db, user, 'reviewer', course_id=course.id):
        raise HTTPException(403, 'Нет назначения на согласование этого курса')
    from app.services.course_publication_service import CoursePublicationService
    modules, _ = CoursePublicationService._validate(course, db)
    snapshot = svc.build_publication_snapshot(db, course, modules)
    return {'content': snapshot, 'content_digest': svc.content_digest(snapshot)}


@router.post('/courses/{course_id}/approvals', status_code=201)
def approve_publication(course_id: int, payload: PublicationApprovalInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    course = svc.org_course(db, user, course_id)
    db.execute(select(Course).where(Course.id == course.id).with_for_update())
    if course.owner_id == user.id or not svc.can_scope(db, user, 'reviewer', course_id=course.id):
        raise HTTPException(403, 'Согласование выполняет другой назначенный проверяющий')
    content = review_content(course_id, db, user)
    if content['content_digest'] != payload.content_digest:
        raise HTTPException(409, 'Содержание изменилось после открытия проверки. Сравните новую версию.')
    latest = db.query(LearningPublicationApproval).filter_by(course_id=course.id, organization_id=course.organization_id,
        content_digest=payload.content_digest).order_by(LearningPublicationApproval.id.desc()).first()
    if latest and latest.reviewer_id == user.id and latest.decision == payload.decision and latest.comment == payload.comment:
        return {'id': latest.id, 'decision': latest.decision, 'revision': latest.revision, 'content_digest': latest.content_digest}
    row = LearningPublicationApproval(course_id=course.id, organization_id=course.organization_id, content_digest=payload.content_digest,
        revision=course.content_revision, reviewer_id=user.id, decision=payload.decision, comment=payload.comment)
    db.add(row)
    db.flush()
    audit(db, user, 'course.approval_recorded', 'course', course.id, organization_id=course.organization_id, decision=row.decision, revision=row.revision)
    db.commit()
    return {'id': row.id, 'decision': row.decision, 'revision': row.revision, 'content_digest': row.content_digest}
