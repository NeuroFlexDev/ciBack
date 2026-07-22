import json
import os
import subprocess
import sys

import pytest
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


@pytest.mark.xfail(
    strict=True,
    reason="app.models does not yet import every mapped model for Alembic metadata",
)
def test_clean_model_import_populates_complete_metadata():
    env = os.environ.copy()
    env.update(
        DATABASE_URL="sqlite:///:memory:",
        JWT_SECRET="test-only-jwt-secret-at-least-32-bytes",
    )
    code = (
        "import json; import app.models; "
        "from app.database.db import Base; "
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
