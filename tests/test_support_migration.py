"""Upgrade existing learner bookmarks without discarding confirmed progress."""
import os
import subprocess
import sys
from sqlalchemy import create_engine, inspect, text


def test_support_migration_backfills_bookmarks_and_downgrade_preserves_progress(tmp_path):
    database = tmp_path / 'support-migration.sqlite'
    env = {**os.environ, 'DATABASE_URL': f'sqlite:///{database.as_posix()}',
        'JWT_SECRET': 'test-only-jwt-secret-at-least-32-bytes'}
    def migrate(*args):
        result = subprocess.run([sys.executable, '-m', 'alembic', *args], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    migrate('upgrade', 'ai_0025')
    engine = create_engine(env['DATABASE_URL'])
    # SQLite defaults to disabled FK enforcement; this fixture isolates migration
    # data conversion from unrelated required parent-domain fixtures.
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO learning_assignments (id, organization_id, course_version_id, user_id, assigned_by, cycle_key, state, rules_revision, rules, progress_revision, completed_lessons, last_lesson_id) VALUES (91,1,1,1,1,'initial','active',1,'{}',3,'[17]',17)"))
    migrate('upgrade', 'head')
    assert 'support_access_grants' in inspect(engine).get_table_names()
    with engine.connect() as connection:
        row = connection.execute(text('SELECT last_item_key, completed_lessons, progress_revision FROM learning_assignments WHERE id=91')).one()
        assert row == ('lesson:17', '[17]', 3)
    migrate('downgrade', 'ai_0025')
    assert 'support_access_grants' not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text('SELECT completed_lessons, progress_revision FROM learning_assignments WHERE id=91')).one() == ('[17]', 3)
    engine.dispose()
