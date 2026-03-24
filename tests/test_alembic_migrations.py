import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "alembic_version",
    "chat_messages",
    "chat_sessions",
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


def run_alembic(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_upgrade_head_creates_expected_schema(repo_tmp_dir):
    db_path = repo_tmp_dir / "alembic" / "upgrade-head.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    env = os.environ.copy()
    env.update(
        {
            "ENV": "dev",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "JWT_SECRET": "test-jwt-secret",
            "JWT_ALG": "HS256",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )

    result = run_alembic("upgrade", "head", env=env)
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        user_columns = {column["name"] for column in inspector.get_columns("users")}
    finally:
        engine.dispose()

    assert EXPECTED_TABLES.issubset(tables)
    assert "role" in user_columns
