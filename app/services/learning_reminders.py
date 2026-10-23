"""Bounded, transactional deadline notifications for the durable mail worker."""
from datetime import datetime, timedelta

from app.core.config import settings
from app.models.administration import AdminEvent
from app.models.course_version import CourseVersion
from app.models.learning import LearningAssignment, LearningProgramEnrollment
from app.models.user import User
from app.services.learning_service import settings_for, utc
from app.services.organization_access import audit, lock_org


def queue_due_reminders(db, *, after_id=0, limit=100, now=None):
    """Queue one reminder per assignment/deadline. Caller commits and advances cursor.

    Organization row locks serialize concurrent schedulers. Mail and the audit
    receipt are in the same transaction; rollback makes the assignment retryable.
    No delivery happens here. A disabled mail configuration does not record success.
    """
    from app.services import mail_service
    if not mail_service.configured():
        return {'queued': 0, 'skipped': 0, 'scanned': 0, 'next_cursor': after_id, 'more': False, 'reason': 'mail_not_configured'}
    now = utc(now) if now else datetime.utcnow()
    limit = max(1, min(int(limit), 1000))
    rows = db.query(LearningAssignment).filter(
        LearningAssignment.id > after_id, LearningAssignment.state == 'active',
        LearningAssignment.completed_at.is_(None), LearningAssignment.due_at.is_not(None),
        LearningAssignment.due_at <= now + timedelta(days=365),
    ).order_by(LearningAssignment.id).limit(limit).all()
    organizations = {oid: lock_org(db, oid) for oid in sorted({r.organization_id for r in rows})}
    windows = {}
    for enrollment in db.query(LearningProgramEnrollment).filter(LearningProgramEnrollment.organization_id.in_(organizations)):
        for aid in enrollment.assignment_ids:
            windows[aid] = enrollment.snapshot.get('reminder_days', 3)
    queued = 0
    for row in rows:
        db.refresh(row)
        person = db.get(User, row.user_id)
        if (not organizations[row.organization_id].is_active or not person or not person.is_active or not person.email
                or person.organization_id != row.organization_id or row.state != 'active' or row.completed_at):
            continue
        if row.prerequisite_id:
            previous = db.get(LearningAssignment, row.prerequisite_id)
            if not previous or not previous.completed_at:
                continue
        due = utc(row.due_at)
        if due > now + timedelta(days=windows.get(row.id, 3)):
            continue
        due_key = due.isoformat()
        existing = db.query(AdminEvent).filter_by(action='learning.reminder_queued', target_type='assignment', target_id=str(row.id)).filter(
            AdminEvent.details['due_at'].as_string() == due_key).first()
        if existing:
            continue
        version = db.get(CourseVersion, row.course_version_id)
        config = settings_for(db, row.organization_id)
        url = settings.PUBLIC_APP_URL.rstrip('/') + f'/learning/{row.id}'
        deadline = due.strftime('%Y-%m-%d %H:%M UTC')
        if config.languages == ['en']:
            subject = f'Lernium — learning deadline: {version.name}'
            text = f'Your assigned course: {version.name}\nDeadline: {deadline}\nOpen your course: {url}\nYour saved answers and progress remain available in your account.'
        else:
            subject = f'Лерниум — срок обучения: {version.name}'
            text = f'Вам назначен курс «{version.name}».\nСрок прохождения: {deadline}\nОткрыть курс: {url}\nСохранённые ответы и прогресс доступны в вашем аккаунте.'
        mail_service.queue_mail(db, person.email, subject, text, expires_at=now + timedelta(days=2),
            deduplication_key=f'learning-reminder:{row.id}:{due_key}', organization_id=row.organization_id)
        audit(db, None, 'learning.reminder_queued', 'assignment', row.id, organization_id=row.organization_id, due_at=due_key)
        db.flush()
        queued += 1
    return {'queued': queued, 'skipped': len(rows) - queued, 'scanned': len(rows),
            'next_cursor': rows[-1].id if rows else after_id, 'more': len(rows) == limit}
