"""Create explicitly labelled, revocable staging-only load-test accounts and content.

Run inside the candidate backend container. The private JSON contains real access
tokens and must be copied to a protected load-generator directory, never a CI log.
No customer account, course or response is modified. Deactivation retains evidence.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.database.db import SessionLocal
import app.models  # register all mappings
from app.models.administration import AdminEvent, Organization
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.lesson import Lesson
from app.models.learning import OrganizationLearningSettings
from app.models.module import Module
from app.models.test import Test as Question
from app.models.theory import Theory
from app.models.user import User
from app.schemas.learning import LearningRules
from app.services.learning_service import create_assignment, create_publication_snapshot

ALLOWED_HOSTS = {'dev.platform.lernium.ru', 'localhost', '127.0.0.1', '::1'}
PREFIX = '_Lernium QA load '


def require_staging(url):
    parsed = urlparse(url)
    if parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ValueError('Load fixtures are restricted to the named staging host or loopback')
    if parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('Remote staging requires HTTPS')
    if settings.ENV.lower() in {'prod', 'production'}:
        raise ValueError('Refusing production environment')


def seed(db, *, users=300, courses=80, lessons=10, run_id=None):
    if not 2 <= users <= 300 or not 4 <= courses <= 100 or not 2 <= lessons <= 20:
        raise ValueError('Fixture size outside reviewed bounds')
    run_id = str(UUID(run_id)) if run_id else str(uuid4())
    if db.query(AdminEvent).filter_by(action='qa.load_fixture_created', target_id=run_id).first():
        raise ValueError('Run already exists; refresh its tokens instead of duplicating it')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    org = Organization(name=PREFIX + run_id, seat_limit=users+2, course_limit=courses+2,
                       source_limit=10, generation_limit=0)
    foreign = Organization(name=PREFIX + run_id + ' isolation', generation_limit=0)
    db.add_all([org, foreign]); db.flush()
    # One random unrecorded password; sessions are independently signed, short-lived JWTs.
    password_hash = hash_password(secrets.token_urlsafe(64))
    def user(index, role='member', organization=org):
        row = User(email=f'{run_id}.{index}@loadtest.invalid', full_name=f'Load fixture {index}',
                   password_hash=password_hash, organization_id=organization.id, organization_role=role)
        db.add(row); db.flush(); return row
    owner = user('owner', 'owner')
    outsiders = user('foreign', organization=foreign)
    learners = [user(str(index)) for index in range(users)]
    rules = LearningRules(allow_ai_assistant=False, allow_ai_grading=False).model_dump()
    db.add(OrganizationLearningSettings(organization_id=org.id, rules=rules,
        features={'learning': True, 'authoring': True, 'assistant': False, 'ai_grading': False,
                  'images': False, 'integrations': False},
        delivery={'qa_load_fixture': run_id}))
    publications = []
    for index in range(courses):
        course = Course(name=f'QA {index+1}: evidence-based practice', description='Synthetic load fixture',
                        owner_id=owner.id, organization_id=org.id, status='ready',
                        publication_status='published', published_at=now, content_revision=1, language='ru')
        db.add(course); db.flush()
        module = Module(course_id=course.id, title='Synthetic training module', position=0)
        db.add(module); db.flush()
        ids = []
        for position in range(lessons):
            lesson = Lesson(module_id=module.id, title=f'Procedure {position+1}', position=position)
            db.add(lesson); db.flush(); ids.append(lesson.id)
            db.add(Theory(lesson_id=lesson.id, content=(
                '## Проверка процедуры\n\nПроверьте исходные данные, выполните действие, '
                'сохраните результат и проверьте доказательство. Это синтетический учебный материал.\n\n') * 30))
        question = Question(module_id=module.id, assessment_scope='module', position=0,
                            question='Which action verifies the result?',
                            answers=json.dumps(['Check evidence', 'Ignore evidence']), correct_answer='Check evidence')
        db.add(question); db.flush()
        version = CourseVersion(course_id=course.id, revision=1, publication_status='published',
            published_at=now, created_by=owner.id, name=course.name, description=course.description, language='ru')
        db.add(version); db.flush()
        create_publication_snapshot(db, course, version, [module])
        publications.append({'course_id': course.id, 'version_id': version.id,
                             'lesson_ids': ids, 'assessment_id': f'test:{question.id}'})
    sessions = []
    per_user = min(courses, 10)
    for index, learner in enumerate(learners):
        assignments = []
        for offset in range(per_user):
            publication = publications[(index * per_user + offset) % courses]
            assignment, _ = create_assignment(db, org_id=org.id, version_id=publication['version_id'],
                user_id=learner.id, actor_id=owner.id, rules=rules, rules_revision=1, cycle_key='qa:' + run_id)
            assignments.append({'id': assignment.id, **publication})
        sessions.append({'user_id': learner.id, 'assignments': assignments})
    details = {'organization_id': org.id, 'foreign_organization_id': foreign.id,
               'users': users, 'courses': courses, 'lessons_per_course': lessons,
               'assignments': users * per_user, 'paid_ai_enabled': False}
    db.add(AdminEvent(actor_id=owner.id, action='qa.load_fixture_created', target_type='load_fixture',
                      target_id=run_id, details=details))
    db.flush()
    return {'format': 1, 'run_id': run_id, 'created_at': now.isoformat()+'Z', **details,
            'owner': {'user_id': owner.id}, 'foreign': {'user_id': outsiders.id}, 'sessions': sessions}


def validate_manifest(db, manifest):
    run_id = str(UUID(manifest['run_id']))
    event = db.query(AdminEvent).filter_by(action='qa.load_fixture_created', target_id=run_id).one_or_none()
    org = db.get(Organization, manifest['organization_id'])
    if not event or not org or org.name != PREFIX + run_id or event.details['organization_id'] != org.id:
        raise ValueError('Manifest does not match this synthetic fixture')
    return event, org


def sessions(db, manifest):
    event, org = validate_manifest(db, manifest)
    if not org.is_active:
        raise ValueError('This fixture was deactivated')
    for entry in [manifest['owner'], manifest['foreign'], *manifest['sessions']]:
        row = db.get(User, entry['user_id'])
        expected = event.details['foreign_organization_id'] if entry is manifest['foreign'] else org.id
        if not row or not row.is_active or row.organization_id != expected or not row.email.startswith(manifest['run_id']+'.') or not row.email.endswith('@loadtest.invalid'):
            raise ValueError('Manifest includes an account outside this fixture')
        entry['token'] = create_access_token(row.id, row.auth_version)
    manifest['tokens_issued_at'] = datetime.now(timezone.utc).isoformat()
    manifest['token_ttl_minutes'] = settings.ACCESS_TOKEN_TTL_MINUTES
    return manifest


def deactivate(db, manifest):
    event, org = validate_manifest(db, manifest)
    org_ids = [org.id, event.details['foreign_organization_id']]
    rows = db.query(User).filter(User.organization_id.in_(org_ids)).all()
    if any(not row.email.startswith(manifest['run_id']+'.') or not row.email.endswith('@loadtest.invalid') for row in rows):
        raise ValueError('Unexpected account found; no changes made')
    for row in rows:
        row.is_active = False
        row.auth_version += 1
    for value in org_ids:
        db.get(Organization, value).is_active = False
    db.add(AdminEvent(action='qa.load_fixture_deactivated', target_type='load_fixture',
        target_id=manifest['run_id'], details={'accounts_deactivated': len(rows), 'data_deleted': False}))
    return {'accounts_deactivated': len(rows), 'data_deleted': False}


def private_write(path, payload):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['seed', 'refresh', 'deactivate'])
    parser.add_argument('--confirm-synthetic', action='store_true', required=True)
    parser.add_argument('--manifest', required=True, help='Private input/output path; never stdout')
    parser.add_argument('--output', help='New private path when refreshing tokens')
    parser.add_argument('--users', type=int, default=300)
    parser.add_argument('--courses', type=int, default=80)
    parser.add_argument('--lessons', type=int, default=10)
    args = parser.parse_args()
    require_staging(settings.PUBLIC_APP_URL)
    if args.command == 'seed' and Path(args.manifest).exists():
        parser.error('Manifest already exists; refusing duplicate creation')
    with SessionLocal() as db:
        if args.command == 'seed':
            manifest = seed(db, users=args.users, courses=args.courses, lessons=args.lessons)
            manifest['base_url'] = settings.PUBLIC_APP_URL.rstrip('/') + '/api'
            sessions(db, manifest)
            private_write(args.manifest, manifest)
            try: db.commit()
            except Exception:
                Path(args.manifest).unlink(); raise
            print(json.dumps({key: manifest[key] for key in ('run_id', 'users', 'courses', 'assignments', 'paid_ai_enabled')}))
        else:
            manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
            if args.command == 'refresh':
                if not args.output: parser.error('--output is required for refresh')
                private_write(args.output, sessions(db, manifest))
                print('Refreshed private session bundle')
            else:
                result = deactivate(db, manifest); db.commit(); print(json.dumps(result))


if __name__ == '__main__':
    main()
