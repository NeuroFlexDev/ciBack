"""Persistent at-least-once delivery with leases, stable IDs, expiry and bounded retries."""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, or_, select, update
from app.core.integration_config import integration_settings
from app.models.integrations import OutboundMessage, WebhookEndpoint
from app.services.integration_secrets import seal, unseal
from app.services import integration_http, mail_service

EVENT_TYPES = {"course.published", "assignment.created", "assignment.revoked", "submission.graded", "invoice.paid", "member.revoked"}


def enqueue_event(db, organization_id, event_type, event_id, data):
    """Use an immutable domain event ID, within the same transaction as the domain mutation."""
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported integration event")
    from app.services.integration_access import integration_enabled
    if not integration_enabled(db, organization_id):
        return
    event = {"id": str(event_id), "type": event_type, "version": "1", "organization_id": organization_id,
             "occurred_at": datetime.now(timezone.utc).isoformat(), "data": data}
    for endpoint in db.query(WebhookEndpoint).filter_by(organization_id=organization_id, enabled=True):
        if event_type not in endpoint.events:
            continue
        key = f"webhook:{endpoint.id}:{event_id}"
        if any(isinstance(item, OutboundMessage) and item.deduplication_key == key for item in db.new) or db.query(OutboundMessage.id).filter_by(deduplication_key=key).first():
            continue
        db.add(OutboundMessage(id=str(uuid.uuid4()), organization_id=organization_id, kind="webhook",
            deduplication_key=key, payload_encrypted=seal({"endpoint_id":endpoint.id, "event":event}),
            available_at=datetime.utcnow()))


def claim(db):
    now = datetime.utcnow()
    available = and_(OutboundMessage.available_at <= now,
        or_(OutboundMessage.status == "pending", and_(OutboundMessage.status == "sending", OutboundMessage.lease_until < now)))
    row = db.execute(select(OutboundMessage).where(available).order_by(OutboundMessage.available_at, OutboundMessage.id)
                     .with_for_update(skip_locked=True).limit(1)).scalar_one_or_none()
    if not row:
        return None
    token = str(uuid.uuid4())
    result = db.execute(update(OutboundMessage).where(OutboundMessage.id == row.id, available).values(
        status="sending", lease_token=token, lease_until=now+timedelta(seconds=integration_settings.OUTBOX_LEASE_SECONDS),
        attempts=OutboundMessage.attempts+1))
    db.commit()
    if result.rowcount != 1:
        return None
    db.refresh(row)
    return row


def deliver(db, row):
    if row.expires_at and row.expires_at <= datetime.utcnow():
        return "expired"
    if row.kind != "mail":
        from app.services.integration_access import integration_enabled
        if not integration_enabled(db, row.organization_id):
            return "cancelled"
    payload = unseal(row.payload_encrypted)
    if row.kind == "mail":
        mail_service.send_account_mail(payload["recipient"], payload["subject"], payload["text"], row.id)
    elif row.kind == "webhook":
        endpoint = db.get(WebhookEndpoint, payload["endpoint_id"])
        if not endpoint or not endpoint.enabled or endpoint.organization_id != row.organization_id:
            return "cancelled"
        body = json.dumps(payload["event"], separators=(",", ":"), ensure_ascii=False).encode()
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        signature = hmac.new(unseal(endpoint.secret_encrypted).encode(), timestamp.encode()+b"."+body, hashlib.sha256).hexdigest()
        integration_http.request("POST", endpoint.url, body, {"Content-Type":"application/json", "X-Lernium-Event-ID": payload["event"]["id"],
            "X-Lernium-Signature":f"t={timestamp},v1={signature}", "Idempotency-Key":row.deduplication_key})
    elif row.kind == "lti_score":
        from app.services.lti_service import deliver_score
        deliver_score(db, payload)
    else:
        raise ValueError("Unknown delivery kind")
    return "delivered"


def process_one(db):
    row = claim(db)
    if row is None:
        return False
    row_id, lease_token, attempts = row.id, row.lease_token, row.attempts
    try:
        status = deliver(db, row)
        values = {"status":status, "lease_token":None, "lease_until":None, "last_error":None,
                  "delivered_at":datetime.utcnow() if status == "delivered" else None,
                  "payload_encrypted":"" if status in {"delivered", "expired", "cancelled"} else row.payload_encrypted}
    except Exception as exc:
        # Do not persist SMTP diagnostics, addresses, response bodies, secrets or token URLs.
        db.rollback()
        values = {"status":"failed" if attempts >= integration_settings.OUTBOX_MAX_ATTEMPTS else "pending",
                  "lease_token":None, "lease_until":None, "last_error":type(exc).__name__,
                  "available_at":datetime.utcnow()+timedelta(seconds=min(3600, 15 * 2 ** (attempts-1)))}
    db.execute(update(OutboundMessage).where(OutboundMessage.id == row_id, OutboundMessage.lease_token == lease_token).values(**values))
    db.commit()
    return True
