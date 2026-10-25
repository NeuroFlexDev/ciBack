"""Link competencies to persisted business positions.

Revision ID: competency_0036
Revises: accounts_0035
"""
from alembic import op
import sqlalchemy as sa


revision = "competency_0036"
down_revision = "accounts_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("competencies") as batch:
        batch.add_column(sa.Column("role_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_competencies_role_id_business_positions",
            "business_positions",
            ["role_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_competencies_role_id", ["role_id"])

    # Existing course-level role assignments are the only unambiguous mapping
    # from a legacy free-text job_role to a persisted role record.
    op.execute(
        sa.text(
            "UPDATE competencies SET role_id = ("
            "SELECT courses.position_id FROM courses "
            "WHERE courses.id = competencies.course_id) "
            "WHERE role_id IS NULL AND EXISTS ("
            "SELECT 1 FROM courses WHERE courses.id = competencies.course_id "
            "AND courses.position_id IS NOT NULL)"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("competencies") as batch:
        batch.drop_index("ix_competencies_role_id")
        batch.drop_constraint(
            "fk_competencies_role_id_business_positions", type_="foreignkey"
        )
        batch.drop_column("role_id")
