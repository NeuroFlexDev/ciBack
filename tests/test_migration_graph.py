import json
import os
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory


EXPECTED_TABLES = {
    "course_modules",
    "course_structure",
    "course_versions",
    "courses",
    "feedback",
    "lesson_versions",
    "lessons",
    "module_versions",
    "modules",
    "tasks",
    "tests",
    "theories",
    "users",
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

    assert json.loads(result.stdout) == []


def test_baseline_upgrade_check_and_downgrade(tmp_path):
    database_path = tmp_path / "migration.sqlite"
    env = os.environ.copy()
    env.update(
        DATABASE_URL=f"sqlite:///{database_path.as_posix()}",
        JWT_SECRET="test-only-jwt-secret-at-least-32-bytes",
    )

    for arguments in (
        ("upgrade", "head"),
        ("check",),
        ("downgrade", "-1"),
    ):
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    code = (
        "import json; "
        "from sqlalchemy import create_engine, inspect; "
        f"engine = create_engine({env['DATABASE_URL']!r}); "
        "print(json.dumps(inspect(engine).get_table_names()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(result.stdout) == ["alembic_version"]
