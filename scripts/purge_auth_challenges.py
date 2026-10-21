"""Expire temporary authentication material without removing active second factors."""
from datetime import datetime,timedelta
from sqlalchemy import delete,update
from app.database.db import SessionLocal
import app.models
from app.models.mfa import UserMFA,MFAChallenge


def run(db,now=None):
    now=now or datetime.utcnow()
    expired=db.execute(update(UserMFA).where(UserMFA.enrollment_expires_at<=now).values(
        pending_secret_encrypted='',enrollment_token_hash=None,enrollment_expires_at=None)).rowcount
    challenges=db.execute(delete(MFAChallenge).where(MFAChallenge.expires_at<now-timedelta(days=1))).rowcount
    return {'expired_enrollments':expired,'removed_old_challenges':challenges}


if __name__=='__main__':
    import json
    with SessionLocal() as db:
        result=run(db);db.commit();print(json.dumps(result))
