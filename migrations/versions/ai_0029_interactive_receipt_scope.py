"""Freeze interactive request tenant; retain unattributable legacy history."""
from alembic import op
import sqlalchemy as sa

revision = "ai_0029"
down_revision = "materials_0028"
branch_labels = None
depends_on = None

TABLES = (("scoped_ai_usage", "ix_scoped_ai_tenant_month"),
          ("learner_assistant_messages", "ix_learner_assistant_tenant_month"))


def upgrade():
    for table, index in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("scope_status", sa.String(24), nullable=False, server_default="legacy_unknown"))
            batch.create_foreign_key(f"fk_{table}_organization", "organizations", ["organization_id"], ["id"])
            batch.create_index(index, ["organization_id", "created_at"])
    # Course and assignment tenant are stable provenance. A current User row
    # is deliberately NOT consulted: membership may have changed since receipt.
    op.execute(sa.text("""UPDATE scoped_ai_usage SET
        organization_id = (SELECT organization_id FROM courses WHERE courses.id = scoped_ai_usage.course_id),
        scope_status = 'resolved'
        WHERE EXISTS (SELECT 1 FROM courses WHERE courses.id = scoped_ai_usage.course_id)"""))
    op.execute(sa.text("""UPDATE learner_assistant_messages SET
        organization_id = (SELECT organization_id FROM learning_assignments
            WHERE learning_assignments.id = learner_assistant_messages.assignment_id),
        scope_status = 'resolved'
        WHERE EXISTS (SELECT 1 FROM learning_assignments
            WHERE learning_assignments.id = learner_assistant_messages.assignment_id
            AND learning_assignments.user_id = learner_assistant_messages.owner_id
            AND learning_assignments.course_version_id = learner_assistant_messages.course_version_id)"""))


def downgrade():
    for table, index in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(index)
            batch.drop_constraint(f"fk_{table}_organization", type_="foreignkey")
            batch.drop_column("scope_status")
            batch.drop_column("organization_id")
