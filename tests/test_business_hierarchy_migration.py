"""Exercise the real upgrade with populated pre-hierarchy accounts."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_existing_accounts_and_invitation_evidence_survive_upgrade(tmp_path):
    database = tmp_path / 'hierarchy.sqlite3'
    env = dict(os.environ, DATABASE_URL='sqlite:///' + database.as_posix(), ENV='test')
    root = Path(__file__).resolve().parents[1]

    def migrate(revision):
        result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', revision], cwd=root, env=env,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr

    migrate('ai_0032')
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO organizations(id,name,kind) VALUES(1,'Company','business'),(2,'School','education')")
        for uid, role, org in [(1, 'owner', 1), (2, 'author', 1), (3, 'member', 1), (4, 'member', 1), (5, 'author', 2)]:
            db.execute('INSERT INTO users(id,email,password_hash,organization_id,organization_role) VALUES(?,?,?,?,?)',
                       (uid, f'person{uid}@migration.test', 'unchanged-hash', org, role))
        db.execute("INSERT INTO invitations(token_hash,email,organization_id,role,invited_by,expires_at,accepted_at) VALUES('evidence','person3@migration.test',1,'member',2,'2100-01-01','2026-01-01')")
        db.execute("INSERT INTO invitations(token_hash,email,organization_id,role,invited_by,expires_at) VALUES('pending','leader@migration.test',1,'reviewer',1,'2100-01-01')")
        db.commit()
    migrate('head')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT organization_role,auth_version,password_hash FROM users WHERE id=2').fetchone() == ('manager', 1, 'unchanged-hash')
        assert db.execute('SELECT manager_id FROM users WHERE id=3').fetchone() == (2,)
        assert db.execute('SELECT manager_id FROM users WHERE id=4').fetchone() == (None,)
        assert db.execute('SELECT organization_role,auth_version FROM users WHERE id=5').fetchone() == ('author', 0)
        assert db.execute("SELECT role FROM invitations WHERE token_hash='pending'").fetchone() == ('manager',)
        assert db.execute("SELECT count(*) FROM admin_events WHERE action='business.hierarchy_migrated'").fetchone() == (1,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
