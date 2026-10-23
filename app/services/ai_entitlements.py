"""Only the platform operator may exempt an organization from product AI quotas.

Authorization, physical storage, provider context, bounded retries and concurrent
capacity remain enforced. Usage is always recorded, including exempt traffic.
"""
from sqlalchemy import select
from app.models.administration import Organization


def unlimited_ai(db, organization_id):
    if organization_id is None:
        return False
    return db.execute(select(Organization.ai_unlimited).where(
        Organization.id == organization_id, Organization.is_active.is_(True))).scalar() is True
