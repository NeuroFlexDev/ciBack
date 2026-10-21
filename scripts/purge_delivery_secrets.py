"""Erase expired delivery payloads and consumed federation handshakes, keeping delivery audit counts."""
from datetime import datetime, timedelta
from sqlalchemy import or_, update
from app.database.db import SessionLocal
import app.models
from app.models.integrations import OutboundMessage, OIDCSession, LTISession


def purge(db):
    now=datetime.utcnow()
    expired=db.execute(update(OutboundMessage).where(OutboundMessage.expires_at<now,OutboundMessage.status.in_(["pending","failed"])).values(status="expired",payload_encrypted="",last_error=None))
    # Failed webhook payloads remain available for a bounded operator replay window.
    old=db.execute(update(OutboundMessage).where(OutboundMessage.created_at<now-timedelta(days=30),OutboundMessage.status=="failed").values(status="expired",payload_encrypted="",last_error=None))
    sessions=0
    for model in (OIDCSession,LTISession):
        sessions+=db.query(model).filter(model.expires_at<now-timedelta(days=1)).delete(synchronize_session=False)
    db.commit();return {"expired_payloads":expired.rowcount+old.rowcount,"expired_handshakes":sessions}


if __name__=="__main__":
    with SessionLocal() as db:print(purge(db))
