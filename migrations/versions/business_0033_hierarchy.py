"""Explicit B2B reporting lines and candidate accounts; retain learning history."""
from alembic import op
import sqlalchemy as sa

revision = 'business_0033'
down_revision = 'ai_0032'
branch_labels = depends_on = None


def upgrade():
    for table in ('users', 'invitations'):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('manager_id', sa.Integer(), nullable=True))
            batch.create_foreign_key('fk_' + table + '_manager', 'users', ['manager_id'], ['id'])
            batch.create_index('ix_' + table + '_manager_id', ['manager_id'])
    with op.batch_alter_table('invitations') as batch:
        batch.drop_constraint('ck_invitations_role', type_='check')
        batch.create_check_constraint('ck_invitations_role', "role IN ('owner','admin','author','reviewer','manager','member','candidate')")
    connection = op.get_bind()
    # Retire legacy B2B staff roles in favour of the requested four-role model.
    # IDs, authored materials, grants and learning records remain unchanged.
    legacy = connection.execute(sa.text("SELECT u.id, u.organization_id, u.organization_role FROM users u JOIN organizations o ON o.id=u.organization_id WHERE o.kind='business' AND u.is_admin=false AND u.organization_role IN ('admin','author','reviewer')")).all()
    events = sa.table('admin_events', sa.column('actor_id', sa.Integer()), sa.column('action'), sa.column('target_type'), sa.column('target_id'), sa.column('details', sa.JSON()))
    for uid, org_id, old_role in legacy:
        connection.execute(sa.text("UPDATE users SET organization_role='manager', auth_version=auth_version+1 WHERE id=:id"), {'id': uid})
        connection.execute(events.insert().values(actor_id=None, action='business.hierarchy_migrated', target_type='user', target_id=str(uid), details={'organization_id': org_id, 'previous_role': old_role, 'role': 'manager'}))
    connection.execute(sa.text("UPDATE invitations SET role='manager' WHERE role IN ('admin','author','reviewer') AND accepted_at IS NULL AND EXISTS (SELECT 1 FROM organizations o WHERE o.id=invitations.organization_id AND o.kind='business')"))
    # Only an explicit invitation by a current company leader proves an existing
    # reporting line. Do not guess from position ownership or assign all staff.
    connection.execute(sa.text("""UPDATE invitations SET manager_id=invited_by
        WHERE role='member' AND EXISTS (SELECT 1 FROM users m JOIN organizations o ON o.id=m.organization_id
        WHERE m.id=invitations.invited_by AND m.organization_id=invitations.organization_id
        AND m.organization_role IN ('manager','admin') AND m.is_active=true AND o.kind='business')"""))
    connection.execute(sa.text("""UPDATE users SET manager_id=(SELECT i.manager_id FROM invitations i
        WHERE i.email=users.email AND i.organization_id=users.organization_id AND i.accepted_at IS NOT NULL
        AND i.manager_id IS NOT NULL ORDER BY i.accepted_at DESC, i.id DESC LIMIT 1)
        WHERE organization_role='member' AND EXISTS (SELECT 1 FROM organizations o WHERE o.id=users.organization_id AND o.kind='business')"""))


def downgrade():
    # Downgrade cannot silently recast candidates as employees.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM users WHERE organization_role='candidate'")).scalar() or connection.execute(sa.text("SELECT count(*) FROM invitations WHERE role='candidate'")).scalar():
        raise RuntimeError('Resolve candidate accounts and invitations before downgrading the hierarchy schema')
    with op.batch_alter_table('invitations') as batch:
        batch.drop_constraint('ck_invitations_role', type_='check')
        batch.create_check_constraint('ck_invitations_role', "role IN ('owner','admin','author','reviewer','manager','member')")
    for table in ('invitations', 'users'):
        with op.batch_alter_table(table) as batch:
            batch.drop_index('ix_' + table + '_manager_id')
            batch.drop_constraint('fk_' + table + '_manager', type_='foreignkey')
            batch.drop_column('manager_id')
