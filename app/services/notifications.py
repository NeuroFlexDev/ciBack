"""Inbox events are transactional. Push is optional, private and per device."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, select
from app.models.notifications import UserNotification
from app.models.integrations import MobilePushDevice, MobileSession, MobilePushPreference, OutboundMessage
from app.models.user import User
from app.services.integration_secrets import seal, unseal

TITLES = {"assignment": "Назначено обучение", "deadline": "Приближается срок", "result": "Ответ проверен",
          "review": "Ответ ожидает проверки", "generation": "Состояние генерации изменилось"}


class DeferredDelivery(Exception):
    def __init__(self, until):
        self.until = until


def publish(db, *, user_id, organization_id, event_key, category, resource_type, resource_id, body, title=None):
    row = db.query(UserNotification).filter_by(user_id=user_id, event_key=event_key).first()
    if row:
        return row
    row = UserNotification(user_id=user_id, organization_id=organization_id, event_key=event_key,
        category=category, resource_type=resource_type, resource_id=resource_id, title=title or TITLES[category], body=body)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        row = db.query(UserNotification).filter_by(user_id=user_id, event_key=event_key).one()
    return row


def queue(db, notification):
    from app.services.mobile_push import configured
    if not configured():
        return
    key = f"notice:{notification.id}"
    if db.query(OutboundMessage.id).filter_by(deduplication_key=key).first():
        return
    db.add(OutboundMessage(id=str(uuid.uuid4()), organization_id=notification.organization_id, kind="push",
        deduplication_key=key, payload_encrypted=seal({"notification_id": notification.id}),
        available_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=2)))


def fanout(db, notification_id):
    note = db.get(UserNotification, notification_id)
    if not note or note.read_at:
        return "cancelled"
    devices = db.query(MobilePushDevice).join(MobileSession).filter(MobileSession.user_id == note.user_id,
        MobileSession.revoked_at.is_(None), MobileSession.expires_at > datetime.utcnow()).all()
    for device in devices:
        key = f"notice-device:{note.id}:{device.id}"
        if db.query(OutboundMessage.id).filter_by(deduplication_key=key).first():
            continue
        db.add(OutboundMessage(id=str(uuid.uuid4()), organization_id=note.organization_id, kind="notice_push",
            deduplication_key=key, payload_encrypted=seal({"notification_id": note.id, "device_id": device.id,
                "session_id": device.session_id, "token_hash": device.token_hash}),
            available_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=2)))
    return "delivered"


def quiet_until(pref, now):
    if not pref or not pref.quiet_enabled:
        return None
    zone = ZoneInfo(pref.timezone)
    local = now.replace(tzinfo=timezone.utc).astimezone(zone)
    start, end = pref.quiet_start, pref.quiet_end
    current = local.strftime("%H:%M")
    inside = start <= current < end if start < end else current >= start or current < end
    if not inside:
        return None
    hour, minute = map(int, end.split(":"))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc).replace(tzinfo=None)


def deliver_device(db, outbox, payload):
    from app.services.mobile_push import configured, EXPO_URL
    from app.core.integration_config import integration_settings
    if not configured():
        raise DeferredDelivery(datetime.utcnow() + timedelta(minutes=15))
    note = db.get(UserNotification, payload["notification_id"])
    device = db.get(MobilePushDevice, payload["device_id"])
    session = db.get(MobileSession, device.session_id) if device else None
    user = db.get(User, note.user_id) if note else None
    now = datetime.utcnow()
    if (not note or note.read_at or not device or not session or not user or not user.is_active
        or session.user_id != user.id or session.id != payload["session_id"] or device.token_hash != payload["token_hash"]
        or session.revoked_at or session.expires_at <= now or session.auth_version != user.auth_version
        or user.organization_id != note.organization_id
        or (user.organization_id and (not user.organization or not user.organization.is_active))):
        return "cancelled"
    if note.resource_type == "assignment":
        from app.models.learning import LearningAssignment
        assignment = db.get(LearningAssignment, note.resource_id)
        if not assignment or assignment.user_id != user.id or assignment.state != "active" or (note.category == "deadline" and assignment.completed_at):
            return "cancelled"
    if note.resource_type == "review":
        from app.models.learning import LearningResponse, LearningAssignment
        from app.services.learning_service import can_review
        response = db.get(LearningResponse, note.resource_id)
        assignment = db.get(LearningAssignment, response.assignment_id) if response else None
        if not assignment or response.status != "pending_review" or not can_review(db, user, assignment):
            return "cancelled"
    pref = db.get(MobilePushPreference, user.id)
    if pref and (not pref.push_enabled or not getattr(pref, f"{note.category}_enabled", False)):
        return "cancelled"
    until = quiet_until(pref, now)
    if until:
        raise DeferredDelivery(until)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    access = integration_settings.EXPO_PUSH_ACCESS_TOKEN.get_secret_value()
    if access:
        headers["Authorization"] = f"Bearer {access}"
    # Names, answers and company data never appear on the lock screen.
    message = {"to": unseal(device.token_encrypted), "title": "Lernium", "body": TITLES[note.category],
        "sound": "default", "ttl": 3600, "channelId": note.category,
        "badge": db.query(UserNotification).filter_by(user_id=user.id, organization_id=user.organization_id, read_at=None).count(),
        "data": {"notification_id": note.id}, "collapseId": f"notice-{note.id}"}
    with urlopen(Request(EXPO_URL, data=json.dumps([message]).encode(), headers=headers, method="POST"), timeout=10) as response:
        result = json.loads(response.read(1_000_000))
    tickets = result.get("data")
    if not isinstance(tickets, list) or len(tickets) != 1:
        raise RuntimeError("Invalid Expo ticket")
    ticket = tickets[0]
    if ticket.get("status") == "error":
        if (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
            db.delete(device)
            return "cancelled"
        raise RuntimeError("Expo rejected notification")
    if ticket.get("status") != "ok" or not ticket.get("id"):
        raise RuntimeError("Missing Expo ticket")
    db.add(OutboundMessage(id=str(uuid.uuid4()), organization_id=note.organization_id, kind="push",
        deduplication_key=f"push-receipt:{outbox.id}", payload_encrypted=seal({"receipt_ids": [ticket["id"]],
            "ticket_tokens": {ticket["id"]: device.token_hash}}),
        available_at=now + timedelta(minutes=15), expires_at=now + timedelta(days=2)))
    return "delivered"


def review_requested(db, assignment, response):
    from app.services.learning_service import can_review
    from app.models.learning import LearningRoleGrant
    scoped_reviewers = select(LearningRoleGrant.user_id).where(
        LearningRoleGrant.organization_id == assignment.organization_id,
        LearningRoleGrant.role == "reviewer")
    reviewers = db.query(User).filter_by(organization_id=assignment.organization_id, is_active=True).filter(
        or_(User.organization_role.in_(["owner", "manager", "admin", "reviewer", "author"]),
            User.id.in_(scoped_reviewers)), User.id != assignment.user_id).all()
    for user in reviewers:
        if not can_review(db, user, assignment):
            continue
        row = publish(db, user_id=user.id, organization_id=assignment.organization_id,
            event_key=f"review:{response.id}:{response.revision}", category="review", resource_type="review",
            resource_id=response.id, body="Поступил ответ на задание. Откройте проверку, чтобы оценить его и оставить обратную связь.")
        queue(db, row)


def generation_finished(db, run, course):
    if run.run_type != "graph_generation" or not course:
        return
    title = "Генерация завершена" if run.status in {"completed", "succeeded"} else "Генерация требует внимания"
    row = publish(db, user_id=run.owner_id, organization_id=course.organization_id,
        event_key=f"generation:{run.id}:{run.attempt}:{run.status}", category="generation",
        resource_type="course", resource_id=course.id, title=title,
        body="Откройте веб-платформу, чтобы просмотреть результат и продолжить работу с материалом.")
    queue(db, row)
