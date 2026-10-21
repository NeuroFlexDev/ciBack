"""Grant a platform administrator from a trusted server console only.

Existing account: python -m scripts.platform_admin grant --email owner@example.org
New account: add --create --password-stdin; pass the password through stdin.
No public API can grant this role. Existing passwords are never changed here.
"""
import argparse
import sys
from app.database.db import SessionLocal
import app.models  # noqa: F401: register ORM relationships
from app.models.user import User
from app.core.security import hash_password
from app.schemas.auth import AuthCredentials
from app.services.organization_access import audit


def grant(db, email: str, password: str | None = None):
    email = AuthCredentials.valid_email(email)
    user = db.query(User).filter(User.email == email).with_for_update().one_or_none()
    if user is None:
        if password is None:
            raise ValueError('Account not found. Create explicitly with --create --password-stdin.')
        if not 12 <= len(password) <= 128:
            raise ValueError('Password must contain 12–128 characters.')
        user = User(email=email, password_hash=hash_password(password))
        db.add(user)
        db.flush()
    elif password is not None:
        raise ValueError('Account already exists. Grant without --create; its password will be preserved.')
    if not user.is_active:
        raise ValueError('Account is disabled. Restore access before granting this role.')
    if not user.is_admin:
        user.is_admin = True
        user.auth_version += 1
        audit(db, None, 'administrator.granted', 'user', user.id)
        db.commit()
    return user.id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['grant'])
    parser.add_argument('--email', required=True)
    parser.add_argument('--create', action='store_true')
    parser.add_argument('--password-stdin', action='store_true')
    args = parser.parse_args()
    if args.create != args.password_stdin:
        parser.error('--create and --password-stdin must be used together')
    password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else None
    try:
        with SessionLocal() as db:
            user_id = grant(db, args.email, password)
        print(f'Administrator access granted to user #{user_id}. Sign in again.')
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
