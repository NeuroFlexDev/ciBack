"""Enqueue due reminders in bounded transactions; SMTP delivery remains in the outbox worker."""
import json
from app.database.db import SessionLocal
import app.models
from app.services.learning_reminders import queue_due_reminders


def run(session_factory=SessionLocal):
    cursor=0
    totals={"queued":0,"skipped":0,"scanned":0}
    while True:
        with session_factory() as db:
            result=queue_due_reminders(db,after_id=cursor,limit=100)
            db.commit()
        for key in ("queued","skipped","scanned"):totals[key]+=result.get(key,0)
        if result.get("reason"):totals["reason"]=result["reason"]
        if not result.get("more"):return totals
        next_cursor=result["next_cursor"]
        if next_cursor<=cursor:raise RuntimeError("Reminder cursor did not advance")
        cursor=next_cursor


if __name__=="__main__":
    print(json.dumps(run()))
