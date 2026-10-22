"""B2B job profiles, employee links and position-specific courses."""
from alembic import op
import sqlalchemy as sa

revision = "business_0031"
down_revision = "ai_0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("business_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(600), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=False),
        sa.Column("competencies", sa.Text(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "name_key", name="uq_business_position_name"))
    op.create_index("ix_business_positions_organization_id", "business_positions", ["organization_id"])
    for table in ("courses", "learning_people", "invitations"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("position_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_" + table + "_business_position", "business_positions", ["position_id"], ["id"])
            batch.create_index("ix_" + table + "_position_id", ["position_id"])
    # Preserve existing B2B job titles; B2G/education records are untouched.
    connection = op.get_bind()
    people = connection.execute(sa.text("SELECT p.user_id, p.organization_id, p.position FROM learning_people p JOIN organizations o ON o.id=p.organization_id WHERE o.kind='business' AND trim(p.position)<>'' ORDER BY p.user_id")).all()
    positions = sa.table("business_positions", sa.column("id", sa.Integer()), sa.column("organization_id", sa.Integer()),
        sa.column("name"), sa.column("name_key"), sa.column("description"), sa.column("responsibilities"), sa.column("competencies"),
        sa.column("is_archived", sa.Boolean()), sa.column("revision", sa.Integer()))
    known = {}
    for user_id, org_id, title in people:
        name = title.strip()
        key = (org_id, name.casefold())
        if key not in known:
            known[key] = connection.execute(positions.insert().values(organization_id=org_id, name=name, name_key=key[1],
                description="", responsibilities="", competencies="", is_archived=False, revision=1).returning(positions.c.id)).scalar_one()
        connection.execute(sa.text("UPDATE learning_people SET position_id=:position_id WHERE user_id=:user_id"), {"position_id": known[key], "user_id": user_id})


def downgrade():
    for table in ("invitations", "learning_people", "courses"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index("ix_" + table + "_position_id")
            batch.drop_constraint("fk_" + table + "_business_position", type_="foreignkey")
            batch.drop_column("position_id")
    op.drop_table("business_positions")
