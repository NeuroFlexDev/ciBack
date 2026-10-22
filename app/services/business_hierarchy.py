"""One company, explicit reporting lines; never infer access from a job title."""
from fastapi import HTTPException
from sqlalchemy import false, or_
from app.models.user import User

LEARNER_ROLES = {'member', 'candidate'}
MANAGER_ROLES = {'manager', 'admin'}  # Preserve legacy administrators as scoped leaders.


def is_business(user):
    return bool(user and user.organization and user.organization.kind == 'business' and not user.is_admin)


def people_filter(user, *, include_self=False):
    """SQL boundary shared by lists, counts, reports and employee mutations."""
    tenant = User.organization_id == user.organization_id
    if not is_business(user) or user.organization_role == 'owner':
        return tenant
    scope = (User.manager_id == user.id) & User.organization_role.in_(LEARNER_ROLES) if user.organization_role in MANAGER_ROLES else false()
    if include_self:
        scope = or_(scope, User.id == user.id)
    return tenant & User.is_admin.is_(False) & scope


def can_manage_person(db, actor, target_id):
    if not is_business(actor) or not actor.is_active or not actor.organization.is_active:
        return False
    if actor.organization_role not in {'owner', *MANAGER_ROLES}:
        return False
    return db.query(User.id).filter(User.id == target_id, people_filter(actor)).first() is not None


def require_person(db, actor, target):
    if target.is_admin or not can_manage_person(db, actor, target.id):
        raise HTTPException(404, 'Участник не найден в вашей команде')


def resolve_manager(db, actor, org_id, role, requested=None):
    if role not in LEARNER_ROLES:
        if requested is not None:
            raise HTTPException(422, 'Руководитель назначается сотруднику или кандидату')
        return None
    if not actor.is_admin and actor.organization_role in MANAGER_ROLES:
        if requested is not None and requested != actor.id:
            raise HTTPException(403, 'Руководитель добавляет участников только в свою команду')
        requested = actor.id
    if requested is not None:
        manager = db.query(User).filter(User.id == requested, User.organization_id == org_id,
            User.organization_role.in_(MANAGER_ROLES), User.is_active.is_(True), User.is_admin.is_(False)).first()
        if not manager:
            raise HTTPException(409, 'Выберите действующего руководителя этой компании')
    return requested


def clear_reporting_lines(db, target):
    """A departed/demoted leader must not retain team access after re-appointment."""
    from app.models.administration import Invitation
    db.query(User).filter(User.manager_id == target.id).update({'manager_id': None})
    db.query(Invitation).filter(Invitation.manager_id == target.id, Invitation.accepted_at.is_(None)).update({'manager_id': None})
