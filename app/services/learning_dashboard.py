"""Read-only dashboard aggregates over the caller's authorized learning scope.

Calendar completion time is not active study time. Activity counts immutable
submitted attempts, not page views, drafts, retries, or an invented timer.
"""
from datetime import datetime, timedelta
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.user import User
from app.models.learning import LearningAssignment, LearningResponse
from app.services import learning_service as svc


def dashboard(db, user, *, days=30, activity_days=7, now=None):
    now = svc.utc(now or datetime.utcnow())
    since = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    activity_since = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=activity_days - 1)
    organization_id = svc.organization(user) if user.organization_id else None
    admin = bool(organization_id and svc.is_manager(db, user))
    report = bool(organization_id and (admin or any(g.role in {'manager', 'reviewer'} for g in svc.role_grants(db, user))))
    query = db.query(LearningAssignment).filter(LearningAssignment.organization_id == organization_id)
    if not report:
        query = query.filter(LearningAssignment.user_id == user.id)
    rows = [row for row in query if not report or svc.can_report(db, user, row)] if organization_id else []
    active = [row for row in rows if row.state == 'active']
    cohort = [row for row in active if since <= svc.utc(row.created_at) <= now]
    completed = [row for row in cohort if row.completed_at and svc.utc(row.completed_at) <= now]
    snapshots = {}
    lesson_count = lessons_done = 0
    course_ids = set()
    for row in active:
        if row.course_version_id not in snapshots:
            snapshots[row.course_version_id] = svc.snapshot_for(db, row.course_version_id)
        snapshot = snapshots[row.course_version_id]
        lesson_ids = {lesson['id'] for module in snapshot['modules'] for lesson in module['lessons']}
        lesson_count += len(lesson_ids)
        lessons_done += len(lesson_ids.intersection(row.completed_lessons or []))
        course_ids.add(db.get(CourseVersion, row.course_version_id).course_id)
    durations = [(svc.utc(row.completed_at) - svc.utc(row.created_at)).total_seconds() / 3600 for row in completed]
    durations = [value for value in durations if value >= 0]
    if admin:
        people = db.query(User).filter(User.organization_id == organization_id, User.is_active.is_(True)).count()
        courses = db.query(Course).filter(Course.organization_id == organization_id, Course.is_deleted.is_(False)).count()
    elif report:
        people, courses = len({row.user_id for row in active}), len(course_ids)
    else:
        people, courses = None, len(course_ids)
    daily = {(activity_since + timedelta(days=index)).date().isoformat(): 0 for index in range(activity_days)}
    assignment_ids = [row.id for row in rows]
    pending = 0
    # Chunk IN clauses so the same implementation works on SQLite and Postgres.
    for offset in range(0, len(assignment_ids), 500):
        responses = db.query(LearningResponse).filter(LearningResponse.assignment_id.in_(assignment_ids[offset:offset + 500]))
        pending += responses.filter(LearningResponse.status == 'pending_review').count()
        for (submitted_at,) in responses.filter(LearningResponse.submitted_at >= activity_since, LearningResponse.submitted_at <= now).with_entities(LearningResponse.submitted_at):
            day = svc.utc(submitted_at).date().isoformat()
            if day in daily:
                daily[day] += 1
    return {
        'scope': 'organization' if admin else 'authorized_team' if report else 'self',
        'period_days': days, 'activity_days': activity_days, 'as_of': now.isoformat() + 'Z',
        'metrics': {
            'assignments_in_period': len(cohort), 'completed_in_period': len(completed),
            'completion_percent': round(100 * len(completed) / len(cohort), 1) if cohort else None,
            'average_completion_hours': round(sum(durations) / len(durations), 2) if durations else None,
            'courses': courses, 'people': people, 'active_assignments': len(active),
            'completed_lessons': lessons_done, 'assigned_lessons': lesson_count,
            'lesson_completion_percent': round(100 * lessons_done / lesson_count, 1) if lesson_count else None,
            'pending_review': pending, 'submitted_attempts': sum(daily.values()),
        },
        'daily': [{'date': day, 'submitted_attempts': count} for day, count in daily.items()],
    }
