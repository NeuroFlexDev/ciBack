"""add chat and lesson scoped versioning fields

Revision ID: cb0f3b8f2f6d
Revises: bc9f6d7a1e2c
Create Date: 2026-03-24 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cb0f3b8f2f6d"
down_revision: str | None = "bc9f6d7a1e2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_column_with_sqlite_batch(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            batch_op.add_column(column)
    else:
        op.add_column(table_name, column)


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("engine", sa.String(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_sessions_id"), "chat_sessions", ["id"], unique=False)
    op.create_index(op.f("ix_chat_sessions_user_id"), "chat_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_chat_sessions_course_id"), "chat_sessions", ["course_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_messages_id"), "chat_messages", ["id"], unique=False)
    op.create_index(op.f("ix_chat_messages_chat_id"), "chat_messages", ["chat_id"], unique=False)

    _add_column_with_sqlite_batch("course_versions", sa.Column("snapshot_data", sa.Text(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("tasks", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("lesson_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_tasks_lesson_id_lessons", "lessons", ["lesson_id"], ["id"])
            batch_op.create_index(op.f("ix_tasks_lesson_id"), ["lesson_id"], unique=False)
        with op.batch_alter_table("tests", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("lesson_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_tests_lesson_id_lessons", "lessons", ["lesson_id"], ["id"])
            batch_op.create_index(op.f("ix_tests_lesson_id"), ["lesson_id"], unique=False)
    else:
        op.add_column("tasks", sa.Column("lesson_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_tasks_lesson_id_lessons", "tasks", "lessons", ["lesson_id"], ["id"])
        op.create_index(op.f("ix_tasks_lesson_id"), "tasks", ["lesson_id"], unique=False)

        op.add_column("tests", sa.Column("lesson_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_tests_lesson_id_lessons", "tests", "lessons", ["lesson_id"], ["id"])
        op.create_index(op.f("ix_tests_lesson_id"), "tests", ["lesson_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("tests", recreate="always") as batch_op:
            batch_op.drop_index(op.f("ix_tests_lesson_id"))
            batch_op.drop_column("lesson_id")
        with op.batch_alter_table("tasks", recreate="always") as batch_op:
            batch_op.drop_index(op.f("ix_tasks_lesson_id"))
            batch_op.drop_column("lesson_id")
        with op.batch_alter_table("course_versions", recreate="always") as batch_op:
            batch_op.drop_column("snapshot_data")
    else:
        op.drop_index(op.f("ix_tests_lesson_id"), table_name="tests")
        op.drop_constraint("fk_tests_lesson_id_lessons", "tests", type_="foreignkey")
        op.drop_column("tests", "lesson_id")

        op.drop_index(op.f("ix_tasks_lesson_id"), table_name="tasks")
        op.drop_constraint("fk_tasks_lesson_id_lessons", "tasks", type_="foreignkey")
        op.drop_column("tasks", "lesson_id")

        op.drop_column("course_versions", "snapshot_data")

    op.drop_index(op.f("ix_chat_messages_chat_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_id"), table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index(op.f("ix_chat_sessions_course_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_user_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_id"), table_name="chat_sessions")
    op.drop_table("chat_sessions")
