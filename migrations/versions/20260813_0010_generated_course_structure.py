"""Add persisted generated course ordering and final assessments.

Revision ID: 20260813_0010
Revises: 20260813_0009
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260813_0010"
down_revision: str | None = "20260813_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_positions(table: str, parent: str) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id, {parent} FROM {table} ORDER BY {parent}, id")
    ).fetchall()
    positions: dict[int | None, int] = {}
    for row_id, parent_id in rows:
        position = positions.get(parent_id, 0)
        connection.execute(
            sa.text(f"UPDATE {table} SET position = :position WHERE id = :id"),
            {"position": position, "id": row_id},
        )
        positions[parent_id] = position + 1


def upgrade() -> None:
    with op.batch_alter_table("modules") as batch:
        batch.add_column(sa.Column("position", sa.Integer(), nullable=True))
    _backfill_positions("modules", "course_id")
    with op.batch_alter_table("modules") as batch:
        batch.alter_column("position", nullable=False)
        batch.create_check_constraint("ck_modules_position_nonnegative", "position >= 0")
        batch.create_index("ix_modules_course_position", ["course_id", "position"])

    with op.batch_alter_table("lessons") as batch:
        batch.add_column(sa.Column("position", sa.Integer(), nullable=True))
    _backfill_positions("lessons", "module_id")
    with op.batch_alter_table("lessons") as batch:
        batch.alter_column("position", nullable=False)
        batch.create_check_constraint("ck_lessons_position_nonnegative", "position >= 0")
        batch.create_index("ix_lessons_module_position", ["module_id", "position"])

    with op.batch_alter_table("tests") as batch:
        batch.add_column(sa.Column("position", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("assessment_scope", sa.String(16), nullable=True))
        batch.add_column(sa.Column("course_id", sa.Integer(), nullable=True))
    op.execute("UPDATE tests SET assessment_scope = 'module'")
    _backfill_positions("tests", "module_id")
    with op.batch_alter_table("tests") as batch:
        batch.alter_column("position", nullable=False)
        batch.alter_column("assessment_scope", nullable=False)
        batch.create_foreign_key("fk_tests_course_id", "courses", ["course_id"], ["id"], ondelete="CASCADE")
        batch.create_check_constraint("ck_tests_position_nonnegative", "position >= 0")
        batch.create_check_constraint("ck_tests_assessment_scope", "assessment_scope IN ('module', 'final')")
        batch.create_check_constraint(
            "ck_tests_parent_scope",
            "(assessment_scope = 'module' AND module_id IS NOT NULL AND course_id IS NULL) "
            "OR (assessment_scope = 'final' AND module_id IS NULL AND course_id IS NOT NULL)",
        )
        batch.create_index("ix_tests_module_position", ["module_id", "position"])
        batch.create_index("ix_tests_course_position", ["course_id", "position"])


def downgrade() -> None:
    with op.batch_alter_table("tests") as batch:
        batch.drop_index("ix_tests_course_position")
        batch.drop_index("ix_tests_module_position")
        batch.drop_constraint("ck_tests_parent_scope", type_="check")
        batch.drop_constraint("ck_tests_assessment_scope", type_="check")
        batch.drop_constraint("ck_tests_position_nonnegative", type_="check")
        batch.drop_constraint("fk_tests_course_id", type_="foreignkey")
        batch.drop_column("course_id")
        batch.drop_column("assessment_scope")
        batch.drop_column("position")
    with op.batch_alter_table("lessons") as batch:
        batch.drop_index("ix_lessons_module_position")
        batch.drop_constraint("ck_lessons_position_nonnegative", type_="check")
        batch.drop_column("position")
    with op.batch_alter_table("modules") as batch:
        batch.drop_index("ix_modules_course_position")
        batch.drop_constraint("ck_modules_position_nonnegative", type_="check")
        batch.drop_column("position")
