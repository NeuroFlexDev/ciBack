from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.administration import Organization, Invitation, AdminEvent
from app.models.user import User
from app.models.course import Course
from app.models.document import Document
from app.models.generation_run import GenerationRun
from app.services.feature_access import require_feature


def audit(db, actor, action, target_type, target_id, **details):
    # Values are controlled by callers; never store passwords, invitation tokens or course text.
    db.add(AdminEvent(actor_id=actor.id if actor else None, action=action, target_type=target_type,
                      target_id=str(target_id), details=details))


def lock_org(db: Session, organization_id: int) -> Organization:
    org = db.execute(select(Organization).where(Organization.id == organization_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if not org:
        raise HTTPException(404, 'Организация не найдена')
    return org


def member_ids(db, org_id):
    return select(User.id).where(User.organization_id == org_id)


def seats(db, org_id):
    now = datetime.utcnow()
    return (db.query(User).filter(User.organization_id == org_id, User.is_active.is_(True)).count()
            + db.query(Invitation).filter(Invitation.organization_id == org_id,
                Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None), Invitation.expires_at > now).count())


def check_seat(db, org):
    if not org.is_active:
        raise HTTPException(409, 'Организация приостановлена')
    if seats(db, org.id) >= org.seat_limit:
        raise HTTPException(409, 'Достигнут лимит участников с учётом действующих приглашений')


def check_course_limit(db: Session, owner_id: int):
    user = db.get(User, owner_id)
    require_feature(db, user.organization_id if user else None, 'authoring')
    if user and user.organization_id:
        from app.models.learning import LearningRoleGrant
        if user.organization_role != 'author' and not db.query(LearningRoleGrant).filter_by(
                organization_id=user.organization_id, user_id=user.id, role='author', scope_type='organization').first():
            raise HTTPException(403, 'Создание курсов требует отдельной роли автора организации')
        org = lock_org(db, user.organization_id)
        if not org.is_active:
            raise HTTPException(403, 'Организация приостановлена')
        count = db.query(Course).filter(Course.organization_id == org.id, Course.is_deleted.is_(False)).count()
        if count >= org.course_limit:
            raise HTTPException(409, 'Достигнут лимит курсов организации. Обратитесь к администратору.')


def check_source_limit(db: Session, owner_id: int, course_id: int):
    user = db.get(User, owner_id)
    require_feature(db, user.organization_id if user else None, 'authoring')
    if user and user.organization_id:
        org = lock_org(db, user.organization_id)
        if not org.is_active:
            raise HTTPException(403, 'Организация приостановлена')
        # New sources are current immediately; replacing a version does not consume another source.
        count = db.query(Document).filter(Document.course_id == course_id, Document.is_deleted.is_(False), Document.is_current.is_(True)).with_entities(Document.document_key).distinct().count()
        if count >= org.source_limit:
            raise HTTPException(409, 'Достигнут лимит источников на курс. Обратитесь к администратору для изменения лимита.')


def check_generation_limit(db: Session, owner_id: int):
    user = db.get(User, owner_id)
    require_feature(db, user.organization_id if user else None, 'authoring')
    if user and user.organization_id:
        org = lock_org(db, user.organization_id)
        if not org.is_active:
            raise HTTPException(403, 'Организация приостановлена')
        start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count = db.query(GenerationRun).filter(GenerationRun.owner_id.in_(member_ids(db, org.id)),
            GenerationRun.run_type == 'graph_generation', GenerationRun.created_at >= start).count()
        if count >= org.generation_limit:
            raise HTTPException(409, 'Достигнут месячный лимит запусков генерации организации')
