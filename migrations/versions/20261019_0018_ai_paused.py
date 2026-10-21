"""Resumable AI outage state, distinct from invalid generation results."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0018"
down_revision = "20261019_0017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_constraint("ck_generation_runs_status", type_="check")
        batch.create_check_constraint("ck_generation_runs_status",
            "status IN ('queued', 'running', 'succeeded', 'completed', 'failed', 'paused')")


def downgrade():
    op.execute(sa.text("UPDATE generation_runs SET status = 'failed' WHERE status = 'paused'"))
    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_constraint("ck_generation_runs_status", type_="check")
        batch.create_check_constraint("ck_generation_runs_status",
            "status IN ('queued', 'running', 'succeeded', 'completed', 'failed')")
