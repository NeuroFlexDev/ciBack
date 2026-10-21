"""Operator-only local maintenance. No public administrative endpoint.

Run inside the backend container. `list` contains personal data: use a private terminal.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json

from app.database.db import SessionLocal
from app.models.legal import LegalReceipt, LegalRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['summary', 'list', 'close', 'purge'])
    parser.add_argument('--reference')
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.action == 'summary':
            print(json.dumps({'open_requests':db.query(LegalRequest).filter_by(status='open').count()}))
        elif args.action == 'list':
            for row in db.query(LegalRequest).filter_by(status='open').order_by(LegalRequest.created_at):
                print(json.dumps({'reference':row.reference,'purpose':row.purpose,'email':row.email,
                                  'name':row.name,'message':row.message,'created_at':row.created_at.isoformat()},ensure_ascii=False))
        elif args.action == 'close':
            if not args.reference: parser.error('--reference is required')
            row = db.get(LegalRequest,args.reference)
            if not row: parser.error('request not found')
            row.status='closed'; row.closed_at=datetime.now(timezone.utc); db.commit()
            print('Request closed; the response must already have been delivered by the operator.')
        elif args.action == 'purge':
            now=datetime.now(timezone.utc)
            # Enquiry text is unnecessary after completion; consent evidence is retained separately.
            count=db.query(LegalRequest).filter(LegalRequest.purpose=='enquiry',
                ((LegalRequest.closed_at < now-timedelta(days=30)) | (LegalRequest.created_at < now-timedelta(days=365)))).delete(synchronize_session=False)
            count+=db.query(LegalRequest).filter(LegalRequest.purpose.in_(['support','rights']),
                LegalRequest.closed_at < now-timedelta(days=1096)).delete(synchronize_session=False)
            # Do not erase current users' contractual evidence or automatically delete their accounts.
            receipts=db.query(LegalReceipt).filter(LegalReceipt.user_id.is_(None),
                LegalReceipt.created_at < now-timedelta(days=1096)).delete(synchronize_session=False)
            db.commit(); print(json.dumps({'requests_deleted':count,'unlinked_receipts_deleted':receipts}))

if __name__=='__main__':main()
