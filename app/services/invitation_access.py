"""Apply operator-configured owner authoring when a personal invitation is accepted."""
from app.models.learning import LearningRoleGrant, OrganizationLearningSettings
from app.services.organization_access import audit


def apply_owner_authoring(db, invitation, user):
    # The acceptance route holds the organization lock and has verified the
    # invitation. The recipient cannot supply this policy in the acceptance form.
    if (user.is_admin or user.organization_id != invitation.organization_id
            or user.organization_role != 'owner' or invitation.role != 'owner'
            or user.organization.kind == 'business'):
        return
    config = db.get(OrganizationLearningSettings, user.organization_id)
    if (not config or config.delivery.get('owner_authoring_on_join') is not True
            or config.features.get('authoring') is False):
        return
    scope = dict(organization_id=user.organization_id, user_id=user.id,
                 role='author', scope_type='organization', scope_id=0)
    if db.query(LearningRoleGrant).filter_by(**scope).first():
        return
    db.add(LearningRoleGrant(**scope, created_by=invitation.invited_by))
    audit(db, None, 'role.granted', 'user', user.id, **scope,
          source='organization_invitation_policy', invitation_id=invitation.id)
