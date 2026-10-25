"""Session-checked Expo delivery and user notification preferences."""
import json
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.core.integration_config import integration_settings
from app.models.integrations import (MobilePushDevice, MobilePushPreference, MobileSession,
                                     OutboundMessage)
from app.models.learning import LearningAssignment
from app.models.user import User
from app.services.integration_secrets import cipher, seal, unseal

EXPO_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
TITLES = {"assignment": "Новое назначение", "deadline": "Скоро срок обучения", "result": "Результат проверки"}
BODY = {"assignment": "Вам доступно новое назначение.", "deadline": "Срок выполнения скоро.", "result": "Проверка завершена."}


def configured():
    if not integration_settings.EXPO_PUSH_ENABLED:
        return False
    try:
        cipher()
        return True
    except Exception:
        return False


def preferences(db, user_id):
    row = db.get(MobilePushPreference, user_id)
    if row is None:
        row = MobilePushPreference(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def enqueue(db, assignment, notification_type, event_key):
    if notification_type not in {"assignment", "result", "deadline"}:
        return
    if not configured():
        return
    key = f"push:{notification_type}:{event_key}"
    if db.query(OutboundMessage.id).filter_by(deduplication_key=key).first():
        return
    db.add(OutboundMessage(id=__import__("uuid").uuid4().hex, organization_id=assignment.organization_id,
        kind="push", deduplication_key=key,
        payload_encrypted=seal({"assignment_id": assignment.id, "type": notification_type}),
        available_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=2)))


def deliver(db, row, payload):
    if payload.get("receipt_ids"):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        access = integration_settings.EXPO_PUSH_ACCESS_TOKEN.get_secret_value()
        if access:
            headers["Authorization"] = f"Bearer {access}"
        request = Request(EXPO_RECEIPTS_URL, data=json.dumps({"ids": payload["receipt_ids"]}).encode(), headers=headers, method="POST")
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read(1_000_000))
        receipts = result.get("data", {})
        for ticket_id, token_hash in payload.get("ticket_tokens", {}).items():
            receipt = receipts.get(ticket_id)
            if receipt is None:
                # Expo may not have created the receipt yet; retry after backoff.
                raise RuntimeError("Expo push receipt is not ready")
            if receipt.get("status") == "error":
                code = (receipt.get("details") or {}).get("error")
                if code == "DeviceNotRegistered":
                    db.query(MobilePushDevice).filter_by(token_hash=token_hash).delete(synchronize_session=False)
                else:
                    raise RuntimeError("Expo push receipt reported a delivery error")
        return "delivered"
    assignment = db.get(LearningAssignment, payload["assignment_id"])
    if not assignment or assignment.state != "active":
        return "cancelled"
    notification_type = payload["type"]
    if notification_type == "deadline" and assignment.completed_at:
        return "cancelled"
    pref = db.get(MobilePushPreference, assignment.user_id)
    allowed = (pref is None or getattr(pref, {"assignment": "assignment_enabled", "deadline": "deadline_enabled", "result": "result_enabled"}[notification_type]))
    if not allowed:
        return "cancelled"
    sessions = db.execute(select(MobilePushDevice, MobileSession).join(MobileSession, MobileSession.id == MobilePushDevice.session_id)
        .where(MobileSession.user_id == assignment.user_id, MobileSession.revoked_at.is_(None),
               MobileSession.expires_at > datetime.utcnow(),
               MobilePushDevice.token_encrypted.is_not(None))).all()
    user = db.get(User, assignment.user_id)
    if not user or not user.is_active or user.organization_id != assignment.organization_id or not user.organization or not user.organization.is_active:
        return "cancelled"
    messages, hashes = [], []
    for device, session in sessions:
        if session.user_id != assignment.user_id or session.revoked_at or session.auth_version != user.auth_version:
            continue
        token = unseal(device.token_encrypted)
        hashes.append(device.token_hash)
        messages.append({"to": token, "title": TITLES[notification_type], "body": BODY[notification_type],
            "sound": "default", "ttl": 120, "data": {"type": notification_type,
                "assignment_id": assignment.id, "route": f"/learning/assignments/{assignment.id}"}})
    if not messages:
        return "cancelled"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    access = integration_settings.EXPO_PUSH_ACCESS_TOKEN.get_secret_value()
    if access:
        headers["Authorization"] = f"Bearer {access}"
    request = Request(EXPO_URL, data=json.dumps(messages).encode(), headers=headers, method="POST")
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read(1_000_000))
    tickets = result.get("data", [])
    if not isinstance(tickets, list) or len(tickets) != len(messages):
        raise RuntimeError("Expo returned an invalid ticket response")
    for index, ticket in enumerate(tickets):
        if ticket.get("status") == "error":
            code = (ticket.get("details") or {}).get("error")
            if code == "DeviceNotRegistered":
                db.query(MobilePushDevice).filter_by(token_hash=hashes[index]).delete(synchronize_session=False)
            elif code not in {"MessageRateExceeded", "MessageTooBig"}:
                raise RuntimeError("Expo rejected push message")
            elif code == "MessageRateExceeded":
                raise RuntimeError("Expo temporarily rate limited push message")
    ticket_tokens = {ticket["id"]: hashes[index] for index, ticket in enumerate(tickets) if ticket.get("status") == "ok" and ticket.get("id")}
    if ticket_tokens:
        import uuid
        now = datetime.utcnow()
        db.add(OutboundMessage(id=str(uuid.uuid4()), organization_id=row.organization_id, kind="push",
            deduplication_key=f"push-receipt:{row.id}",
            payload_encrypted=seal({"receipt_ids": list(ticket_tokens), "ticket_tokens": ticket_tokens}),
            available_at=now + timedelta(minutes=15), expires_at=now + timedelta(days=2)))
    return "delivered"


def enqueue_due_reminders(db, now=None, limit=1000, after_id=0):
    """Queue due reminders at 09:00 local time, once per assignment and due date."""
    now = now or datetime.now(timezone.utc)
    queued = 0
    rows = db.query(LearningAssignment).filter(LearningAssignment.id > after_id, LearningAssignment.state == "active",
        LearningAssignment.completed_at.is_(None), LearningAssignment.due_at.is_not(None)).order_by(LearningAssignment.id).limit(limit).all()
    for assignment in rows:
        if assignment.prerequisite_id:
            prerequisite = db.get(LearningAssignment, assignment.prerequisite_id)
            if not prerequisite or not prerequisite.completed_at:
                continue
        pref = db.get(MobilePushPreference, assignment.user_id)
        if pref and not pref.deadline_enabled:
            continue
        try:
            zone = ZoneInfo(pref.timezone if pref else "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("UTC")
        local_now = now.astimezone(zone)
        due_utc = assignment.due_at.replace(tzinfo=timezone.utc) if assignment.due_at.tzinfo is None else assignment.due_at.astimezone(timezone.utc)
        due_local = due_utc.astimezone(zone)
        n = 3
        from app.models.learning import LearningProgramEnrollment
        enrollment = next((item for item in db.query(LearningProgramEnrollment).filter_by(organization_id=assignment.organization_id)
                           if assignment.id in (item.assignment_ids or [])), None)
        if enrollment:
            n = max(0, min(365, int((enrollment.snapshot or {}).get("reminder_days", 3))))
        if local_now.date() == due_local.date() - timedelta(days=n) and (local_now.hour, local_now.minute) >= (9, 0):
            before = db.query(OutboundMessage.id).filter_by(deduplication_key=f"push:deadline:{assignment.id}:{due_local.date().isoformat()}").first()
            if not before:
                enqueue(db, assignment, "deadline", f"{assignment.id}:{due_local.date().isoformat()}")
                queued += 1
    return {"queued": queued, "next_cursor": rows[-1].id if rows else after_id, "more": len(rows) == limit}
