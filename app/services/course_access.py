"""SQL predicates shared by author APIs and background workers."""
from sqlalchemy import and_, exists, or_, select
from app.models.course import Course
from app.models.user import User
from app.models.learning import LearningRoleGrant
from app.models.administration import Organization


def course_access_filter(user_id):
    org_id = select(User.organization_id).where(User.id == user_id).scalar_subquery()
    author_role = exists(select(User.id).where(User.id == user_id, User.is_active.is_(True), User.organization_role == 'author'))
    organization_grant = exists(select(LearningRoleGrant.id).where(
        LearningRoleGrant.user_id == user_id, LearningRoleGrant.organization_id == Course.organization_id,
        LearningRoleGrant.role == 'author', LearningRoleGrant.scope_type == 'organization', LearningRoleGrant.scope_id == 0))
    course_grant = exists(select(LearningRoleGrant.id).where(
        LearningRoleGrant.user_id == user_id, LearningRoleGrant.organization_id == Course.organization_id,
        LearningRoleGrant.role == 'author', LearningRoleGrant.scope_type == 'course', LearningRoleGrant.scope_id == Course.id))
    active = exists(select(User.id).where(User.id == user_id, User.is_active.is_(True)))
    return and_(active, or_(
        and_(Course.organization_id.is_(None), org_id.is_(None), Course.owner_id == user_id),
        and_(Course.organization_id == org_id,
             exists(select(Organization.id).where(Organization.id == Course.organization_id, Organization.is_active.is_(True))),
             or_(course_grant, and_(Course.owner_id == user_id, or_(author_role, organization_grant))))))


def related_course_access(course_id_column, user_id):
    return course_id_column.in_(select(Course.id).where(course_access_filter(user_id)))
