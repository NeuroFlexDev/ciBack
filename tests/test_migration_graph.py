import json
import os
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory


BASE_TABLES = {
    "approvals",
    "assessment_rubrics",
    "chat_messages",
    "chats",
    "competencies",
    "course_modules",
    "course_structure",
    "course_versions",
    "course_graphs",
    "course_generation_settings",
    "courses",
    "document_chunks",
    "documents",
    "feedback",
    "generation_runs",
    "lesson_versions",
    "lessons",
    "learning_events",
    "learning_objectives",
    "module_versions",
    "modules",
    "tasks",
    "tests",
    "test_versions",
    "theories",
    "users",
}
EXPECTED_TABLES = BASE_TABLES | {
    "ai_calls", "ai_response_cache", "chat_memory", "chunk_embeddings", "canvas_workspaces",
    "agent_artifacts",
    "course_source_links",
    "course_update_proposals",
    "password_resets",
    "organizations",
    "invitations",
    "admin_events",
    "legal_receipts",
    "legal_requests",
}

LEARNING_TABLES = {
    "organization_learning_settings", "learning_role_grants", "learning_departments", "learning_people",
    "learning_groups", "learning_group_members", "published_learning_content", "learning_assessment_definitions",
    "learning_assignments", "learning_group_enrollments", "learning_responses", "learning_grade_events",
    "learning_programs", "learning_program_enrollments", "organization_ownership_transfers", "learning_publication_approvals",
}
MATERIAL_TABLES = {"organization_material_policy", "scoped_ai_usage", "course_images", "learner_assistant_messages"}
INTEGRATION_TABLES = {
    "outbound_messages", "integration_keys", "integration_operations", "webhook_endpoints",
    "oidc_providers", "external_identities", "oidc_sessions", "billing_accounts", "billing_invoices",
    "payment_events", "lti_platforms", "lti_resources", "lti_identities", "lti_sessions", "lti_grade_bindings",
}
PRE_INTEGRATION_TABLES = EXPECTED_TABLES | LEARNING_TABLES | MATERIAL_TABLES
AI_RESOURCE_TABLES = {"ai_resource_mutex", "ai_resource_usage", "ai_thread_references"}
EXPECTED_TABLES = PRE_INTEGRATION_TABLES | INTEGRATION_TABLES | AI_RESOURCE_TABLES | {
    "business_positions", "support_access_grants", "user_mfa", "mfa_recovery_codes", "mfa_challenges", "published_source_files",
}

def test_alembic_revision_graph_has_one_connected_head():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())
    revision_ids = {revision.revision for revision in revisions}

    assert len(script.get_heads()) == 1
    for revision in revisions:
        down_revisions = revision._normalized_down_revisions
        assert set(down_revisions) <= revision_ids


def test_clean_model_import_populates_complete_metadata():
    env = os.environ.copy()
    env.update(
        DATABASE_URL="sqlite:///:memory:",
        JWT_SECRET="test-only-jwt-secret-at-least-32-bytes",
    )
    code = (
        "import json; import app.models; "
        "from sqlalchemy.orm import configure_mappers; "
        "from app.database.db import Base; "
        "configure_mappers(); "
        "print(json.dumps(sorted(Base.metadata.tables)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert set(json.loads(result.stdout)) == EXPECTED_TABLES


def test_application_import_does_not_create_database_schema(tmp_path):
    database_path = tmp_path / "startup.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    env = os.environ.copy()
    env.update(
        DATABASE_URL=database_url,
        JWT_SECRET="test-only-jwt-secret-at-least-32-bytes",
    )
    code = (
        "import json; import main; "
        "from sqlalchemy import create_engine, inspect; "
        f"engine = create_engine({database_url!r}); "
        "print(json.dumps(inspect(engine).get_table_names()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(result.stdout.splitlines()[-1]) == []


def test_migrations_upgrade_check_and_downgrade(tmp_path):
    database_path = tmp_path / "migration.sqlite"
    env = os.environ.copy()
    env.update(
        DATABASE_URL=f"sqlite:///{database_path.as_posix()}",
        JWT_SECRET="test-only-jwt-secret-at-least-32-bytes",
    )

    for arguments in (("upgrade", "head"), ("check",), ("downgrade", "ai_0022")):
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    inspect_code = (
        "import json; "
        "from sqlalchemy import create_engine, inspect; "
        f"engine = create_engine({env['DATABASE_URL']!r}); "
        "print(json.dumps(inspect(engine).get_table_names()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", inspect_code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    # Roll back to an explicit release boundary, independently of later heads.
    assert set(json.loads(result.stdout)) == PRE_INTEGRATION_TABLES | {"alembic_version"}
    column_code = (
        "import json; from sqlalchemy import create_engine, inspect; "
        f"engine = create_engine({env['DATABASE_URL']!r}); "
        "print(json.dumps([c['name'] for c in inspect(engine).get_columns('ai_calls')]))"
    )
    columns = subprocess.run([sys.executable, "-c", column_code], check=True,
                             capture_output=True, text=True, env=env)
    assert "routing" in json.loads(columns.stdout)

    for arguments in (("upgrade", "head"), ("check",), ("downgrade", "base")):
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    result = subprocess.run(
        [sys.executable, "-c", inspect_code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(result.stdout) == ["alembic_version"]
