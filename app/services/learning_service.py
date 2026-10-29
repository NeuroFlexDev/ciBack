"""Learning authorization, immutable publication and server-side assessment rules."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.administration import Organization
from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.user import User
from app.models.learning import (
    AssessmentDefinition, LearningAssignment, LearningDepartment, LearningGradeEvent,
    LearningGroup, LearningGroupMember, LearningPerson, LearningResponse,
    LearningRoleGrant, OrganizationLearningSettings, PublishedLearningContent, LearningPublicationApproval,
)
from app.schemas.learning import LearningRules
from app.services.organization_access import audit


def event(db, org_id, event_type, event_id, data):
    import importlib.util
    if org_id and importlib.util.find_spec('app.services.outbox_service'):
        from app.services.outbox_service import enqueue_event
        enqueue_event(db, org_id, event_type, event_id, data)


def ensure_learning_write(db, org_id):
    import importlib.util
    if importlib.util.find_spec('app.services.billing_service'):
        from app.services.billing_service import ensure_write_access
        ensure_write_access(db, org_id)


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


def organization(user):
    if not user.organization_id:
        raise HTTPException(409, 'Аккаунт не подключён к организации')
    if not user.is_active or not user.organization or not user.organization.is_active:
        raise HTTPException(403, 'Доступ к организации приостановлен')
    return user.organization_id


def settings_for(db, org_id, lock=False):
    if lock:
        db.execute(select(Organization).where(Organization.id == org_id).with_for_update()).scalar_one()
    row = db.get(OrganizationLearningSettings, org_id)
    if row is None:
        org = db.get(Organization, org_id)
        row = OrganizationLearningSettings(organization_id=org_id, profile=org.kind if org else 'business', rules=LearningRules().model_dump())
        db.add(row)
        db.flush()
    return row


def role_grants(db, user):
    from app.services.business_hierarchy import is_business
    if is_business(user) and user.organization_role in {'member', 'candidate'}:
        return []
    return db.query(LearningRoleGrant).filter_by(organization_id=user.organization_id, user_id=user.id).all()


def is_manager(db, user):
    from app.services.business_hierarchy import is_business
    if is_business(user):
        return user.organization_role == 'owner'
    return user.organization_role in {'owner', 'admin'} or any(g.role == 'admin' and g.scope_type == 'organization' for g in role_grants(db, user))


def require_manager(db, user):
    org_id = organization(user)
    if not is_manager(db, user):
        raise HTTPException(403, 'Управление доступно администратору вашей организации')
    return org_id


def member(db, org_id, user_id, active=True):
    query = db.query(User).filter(User.id == user_id, User.organization_id == org_id)
    if active:
        query = query.filter(User.is_active.is_(True))
    row = query.first()
    if row is None:
        raise HTTPException(404, 'Участник не найден в вашей организации')
    return row


def group(db, org_id, group_id, active=True):
    query = db.query(LearningGroup).filter_by(id=group_id, organization_id=org_id)
    if active:
        query = query.filter(LearningGroup.is_archived.is_(False))
    row = query.first()
    if not row:
        raise HTTPException(404, 'Группа не найдена')
    return row


def can_scope(db, user, role, *, course_id=None, group_id=None, user_id=None):
    """Roles that expose work/answers always require an explicit object scope."""
    for grant in role_grants(db, user):
        if grant.role != role:
            continue
        if grant.scope_type == 'course' and grant.scope_id == course_id:
            return True
        if grant.scope_type == 'group':
            if grant.scope_id == group_id:
                return True
            if user_id and db.get(LearningGroupMember, (grant.scope_id, user_id)):
                return True
        if grant.scope_type == 'department' and user_id:
            person = db.get(LearningPerson, user_id)
            if person and person.organization_id == user.organization_id and person.department_id == grant.scope_id:
                return True
    return False


def course_org(db, course):
    if course.organization_id:
        return course.organization_id
    # Only pre-migration/personal courses have no boundary. Bind once; never follow
    # a former employee into another organization.
    owner = db.get(User, course.owner_id) if course.owner_id else None
    if owner and owner.organization_id:
        course.organization_id = owner.organization_id
        db.flush()
    return course.organization_id


def org_course(db, user, course_id):
    org_id = organization(user)
    course = db.query(Course).filter_by(id=course_id).first()
    if not course or course_org(db, course) != org_id:
        raise HTTPException(404, 'Курс не найден')
    return course


def allowed_assigner(db, user, course, target_id=None, group_id=None):
    from app.services.business_hierarchy import is_business, can_manage_person
    if is_business(user):
        if user.organization_role == 'owner':
            return
        if group_id is not None or target_id is None or not can_manage_person(db, user, target_id):
            raise HTTPException(403, 'Назначать регламенты и тесты можно только участникам своей команды')
        return
    if is_manager(db, user):
        return
    grants = role_grants(db, user)
    catalogue = any(g.role == 'manager' and g.scope_type == 'course' and g.scope_id == course.id for g in grants)
    scope = can_scope(db, user, 'manager', group_id=group_id, user_id=target_id)
    if not catalogue or not scope:
        raise HTTPException(403, 'Нет права назначать этот курс выбранной группе или участнику')


def _answers(raw):
    try:
        value = json.loads(raw or '[]')
    except (TypeError, ValueError):
        value = []
    return [str(x) for x in value] if isinstance(value, list) else []


def _assessment(db, item, kind):
    definition = db.query(AssessmentDefinition).filter(
        AssessmentDefinition.test_id == item.id if kind == 'test' else AssessmentDefinition.task_id == item.id).first()
    default_correct = [item.correct_answer] if kind == 'test' and item.correct_answer else []
    criteria = deepcopy(definition.criteria) if definition else []
    if kind == 'task' and not criteria:
        for rubric in item.assessment_rubrics:
            if not rubric.is_deleted:
                criteria.extend(deepcopy(rubric.criteria or []))
    if not definition:
        normalized = []
        for criterion in criteria:
            if 'max_points' in criterion:
                normalized.append(criterion)
                continue
            # Generated rubrics use weighted levels; preserve their scale in the immutable version.
            levels = criterion.get('levels') or []
            weight = criterion.get('weight')
            if isinstance(weight, (int, float)) and levels and all(isinstance(level.get('score'), (int, float)) for level in levels):
                normalized.append({'id': criterion.get('id'), 'title': criterion.get('title'),
                    'description': criterion.get('description', ''),
                    'max_points': weight * max(level['score'] for level in levels),
                    'levels': [{'title': level.get('title', ''), 'description': level.get('description', ''), 'points': weight * level['score']} for level in levels]})
            else:
                normalized.append(criterion)  # Publication validation reports incomplete legacy rubrics.
        criteria = normalized
    return {
        'id': f'{kind}:{item.id}', 'revision': definition.revision if definition else getattr(item, 'revision', 1),
        'question': item.question if kind == 'test' else item.name,
        'description': '' if kind == 'test' else item.description or '',
        'question_type': definition.question_type if definition else ('single' if kind == 'test' else 'open'),
        'answers': _answers(item.answers) if kind == 'test' else [],
        'max_points': definition.max_points if definition else sum(c.get('max_points', 0) for c in criteria) if criteria else 1.0,
        'criteria': criteria,
        'grading_mode': definition.grading_mode if definition else ('automatic' if kind == 'test' else 'manual'),
        'correct_answers': deepcopy(definition.correct_answers) if definition else default_correct,
    }


def build_publication_snapshot(db, course, modules):
    data = {'course': {'id': course.id, 'title': course.name, 'description': course.description or '', 'revision': course.content_revision}, 'modules': [], 'assessments': []}
    from app.services.source_release import manifest
    data['sources'] = manifest(db, course.id)
    for module in modules:
        data['modules'].append({
            'id': module.id, 'title': module.title, 'description': module.description or '',
            'lessons': [{'id': l.id, 'title': l.title, 'description': l.description or '', 'content': l.theory.content if l.theory and not l.theory.is_deleted else ''}
                        for l in sorted(module.lessons, key=lambda x: (x.position, x.id)) if not l.is_deleted],
            'assessments': [_assessment(db, t, 'test') for t in sorted(module.tests, key=lambda x: (x.position, x.id)) if not t.is_deleted]
                           + [_assessment(db, t, 'task') for t in sorted(module.tasks, key=lambda x: x.id) if not t.is_deleted],
        })
    data['assessments'] = [_assessment(db, t, 'test') for t in sorted(course.final_tests, key=lambda x: (x.position, x.id)) if not t.is_deleted]
    # Pin only approved images that are actually referenced by learner-visible
    # snapshot content. Metadata lives with the immutable publication itself.
    from app.services.protected_image_refs import image_ids_in
    from app.models.materials import CourseImage
    from hashlib import sha256
    from app.services.image_service import storage as image_storage
    image_ids = image_ids_in(data)
    data['media'] = []
    for image_id in sorted(image_ids):
        image = db.get(CourseImage, image_id)
        if not image or image.course_id != course.id or image.status != 'approved' or not image.storage_key:
            continue
        content = image_storage().read_bytes(image.storage_key)
        data['media'].append({'id': image.id, 'type': 'image', 'version': 1,
            'mime_type': image.mime_type, 'size_bytes': len(content),
            'sha256': sha256(content).hexdigest(), 'storage_key': image.storage_key})
    return data


def validate_assessment_publication(db, course, modules):
    from pydantic import ValidationError
    from app.schemas.learning import AssessmentInput
    problems = []
    for item in assessments(build_publication_snapshot(db, course, modules)):
        try:
            AssessmentInput.model_validate({k: item[k] for k in ['question_type', 'correct_answers', 'max_points', 'grading_mode']} | {
                'criteria': item['criteria']})
            if item['question_type'] != 'open' and (not item['answers'] or len(set(item['answers'])) != len(item['answers']) or any(answer not in item['answers'] for answer in item['correct_answers'])):
                raise ValueError('Укажите разные варианты и правильный ответ из их числа')
        except (ValidationError, ValueError, TypeError, KeyError):
            problems.append({'id': item['id'], 'question': item['question'],
                'message': 'Проверьте тип ответа, ключ, баллы и критерии оценивания. Открытый ответ требует критериев и проверки преподавателем.'})
    if problems:
        raise HTTPException(422, {'code': 'assessment_not_ready',
            'message': 'Перед публикацией завершите настройки оценивания заданий', 'assessments': problems})


def content_digest(snapshot):
    import hashlib
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_publication_approval(db, course, modules):
    org_id = course_org(db, course)
    if not org_id:
        return
    config = settings_for(db, org_id)
    if not config.rules.get('require_independent_approval'):
        return
    digest = content_digest(build_publication_snapshot(db, course, modules))
    latest = db.query(LearningPublicationApproval).filter_by(course_id=course.id, organization_id=org_id, content_digest=digest).order_by(LearningPublicationApproval.id.desc()).first()
    if not latest or latest.decision != 'approved' or latest.reviewer_id == course.owner_id:
        raise HTTPException(409, 'Правила организации требуют независимого согласования текущей версии курса')
    reviewer = db.get(User, latest.reviewer_id)
    if not reviewer or not reviewer.is_active or reviewer.organization_id != org_id or not can_scope(db, reviewer, 'reviewer', course_id=course.id):
        raise HTTPException(409, 'Права согласовавшего сотрудника отозваны. Требуется новое согласование.')


def create_publication_snapshot(db, course, version, modules):
    """Run in the same transaction as publication; never update this row later."""
    if db.get(PublishedLearningContent, version.id):
        raise HTTPException(409, 'Опубликованный снимок уже существует')
    data = build_publication_snapshot(db, course, modules)
    from app.services.source_release import pin_sources
    pin_sources(db, version.id, data.get('sources', []))
    db.add(PublishedLearningContent(course_version_id=version.id, organization_id=course_org(db, course), snapshot=data))
    db.flush()
    event(db, course.organization_id, 'course.published', f'course-version:{version.id}:published', {'course_id': course.id, 'course_version_id': version.id, 'revision': version.revision})
    return data


def snapshot_for(db, version_id):
    row = db.get(PublishedLearningContent, version_id)
    if row:
        return row.snapshot
    version = db.get(CourseVersion, version_id)
    if not version or version.publication_status != 'published':
        raise HTTPException(404, 'Опубликованная версия не найдена')
    # Old releases did not snapshot practical assignments. Never invent missing
    # content from today's draft: require a new complete publication instead.
    raise HTTPException(409, 'Эта версия опубликована до полного учебного снимка. Автору нужно выпустить новую версию для назначения.')


def assessments(snapshot):
    return [a for m in snapshot.get('modules', []) for a in m.get('assessments', [])] + snapshot.get('assessments', [])


def assessment_for(db, assignment, key):
    for item in assessments(snapshot_for(db, assignment.course_version_id)):
        if item['id'] == key:
            return item
    raise HTTPException(404, 'Задание не найдено в назначенной версии')


def public_snapshot(db, assignment):
    from app.services.public_assessment_text import public_assessment_text
    data = deepcopy(snapshot_for(db, assignment.course_version_id))
    for item in assessments(data):
        # Explicit whitelist avoids leaking future private metadata fields.
        safe = {k: v for k, v in item.items() if k in {'id', 'revision', 'question', 'description', 'question_type', 'answers', 'max_points', 'criteria', 'grading_mode'}}
        item.clear()
        item.update(safe)
        item['description'] = public_assessment_text(item.get('description', ''))
        item['criteria'] = [{k: v for k, v in c.items() if k in {'id', 'title', 'description', 'max_points', 'levels'}} for c in item.get('criteria', []) if isinstance(c, dict)]
    return data


def learner_assignment(db, user, assignment_id, *, lock=False, allow_revoked=False, allow_locked=False):
    org_id = organization(user)
    query = select(LearningAssignment).where(LearningAssignment.id == assignment_id, LearningAssignment.user_id == user.id, LearningAssignment.organization_id == org_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.execute(query).scalar_one_or_none()
    if not row:
        raise HTTPException(404, 'Назначение не найдено')
    if row.state != 'active' and not allow_revoked:
        raise HTTPException(403, 'Назначение отозвано. Сохранённые результаты доступны в истории.')
    if row.prerequisite_id and not allow_locked:
        previous = db.get(LearningAssignment, row.prerequisite_id)
        if not previous or not previous.completed_at:
            raise HTTPException(409, 'Сначала завершите предыдущий курс программы')
    return row


def authorized_learning_image(db, user, image_id):
    if not user.organization_id:
        return False
    from app.models.materials import CourseImage
    from app.services.protected_image_refs import image_ids_in
    image = db.get(CourseImage, image_id)
    course = db.get(Course, image.course_id) if image else None
    if not image or image.status != 'approved' or not course or course.organization_id != user.organization_id:
        return False
    query = db.query(LearningAssignment).join(CourseVersion, CourseVersion.id == LearningAssignment.course_version_id).filter(
        LearningAssignment.user_id == user.id, LearningAssignment.organization_id == user.organization_id,
        LearningAssignment.state == 'active', CourseVersion.course_id == image.course_id)
    for row in query:
        if row.prerequisite_id:
            previous = db.get(LearningAssignment, row.prerequisite_id)
            if not previous or not previous.completed_at:
                continue
        snapshot = db.get(PublishedLearningContent, row.course_version_id)
        if snapshot and snapshot.organization_id == row.organization_id and image_id in image_ids_in(snapshot.snapshot):
            return True
    return False


def assignment_summary(db, row):
    version = db.get(CourseVersion, row.course_version_id)
    snapshot = snapshot_for(db, row.course_version_id)
    lesson_ids = {l['id'] for m in snapshot['modules'] for l in m['lessons']}
    all_assessments = assessments(snapshot)
    responses = db.query(LearningResponse).filter_by(assignment_id=row.id).all()
    best = {}
    for response in responses:
        if response.status == 'reviewed' and (response.assessment_id not in best or response.points > best[response.assessment_id]):
            best[response.assessment_id] = response.points
    max_points = sum(a['max_points'] for a in all_assessments)
    points = sum(best.get(a['id'], 0) for a in all_assessments)
    locked = bool(row.prerequisite_id and not db.get(LearningAssignment, row.prerequisite_id).completed_at)
    return {
        'id': row.id, 'course_id': version.course_id, 'course_version_id': row.course_version_id,
        'title': version.name, 'revision': version.revision, 'user_id': row.user_id, 'group_id': row.group_id,
        'state': row.state, 'status': 'revoked' if row.state != 'active' else 'locked' if locked else 'completed' if row.completed_at else 'in_progress' if row.completed_lessons or responses else 'assigned',
        'due_at': row.due_at, 'created_at': row.created_at, 'completed_at': row.completed_at,
        'completed_lessons': row.completed_lessons, 'lesson_count': len(lesson_ids), 'last_lesson_id': row.last_lesson_id,
        'last_item_key': row.last_item_key,
        'progress_revision': row.progress_revision, 'progress_percent': round(100 * len(set(row.completed_lessons) & lesson_ids) / len(lesson_ids)) if lesson_ids else 0,
        'points': points, 'max_points': max_points, 'score_percent': round(100 * points / max_points, 2) if max_points else None,
        'pending_reviews': sum(r.status == 'pending_review' for r in responses), 'rules': row.rules, 'rules_revision': row.rules_revision,
    }


def update_completion(db, row):
    summary = assignment_summary(db, row)
    snapshot = snapshot_for(db, row.course_version_id)
    assessment_ids = {a['id'] for a in assessments(snapshot)}
    reviewed_ids = {r.assessment_id for r in db.query(LearningResponse).filter_by(assignment_id=row.id, status='reviewed')}
    lessons_done = not row.rules.get('require_all_lessons', True) or summary['progress_percent'] == 100
    assessments_done = not row.rules.get('require_all_assessments', True) or reviewed_ids >= assessment_ids
    score_done = not assessment_ids or summary['score_percent'] >= row.rules.get('pass_percent', 60)
    if lessons_done and assessments_done and score_done:
        if not row.completed_at:
            row.completed_at = datetime.utcnow()
    else:
        row.completed_at = None
    if row.completed_at and summary['max_points']:
        import importlib.util
        if importlib.util.find_spec('app.services.lti_service'):
            from app.services.lti_service import queue_grade
            queue_grade(db, row.id, summary['points'], summary['max_points'], datetime.utcnow())


def can_review(db, user, assignment):
    if assignment.organization_id != organization(user):
        return False
    from app.services.business_hierarchy import is_business, can_manage_person
    if is_business(user):
        return can_manage_person(db, user, assignment.user_id)
    version = db.get(CourseVersion, assignment.course_version_id)
    return can_scope(db, user, 'reviewer', course_id=version.course_id, group_id=assignment.group_id, user_id=assignment.user_id)


def can_report(db, user, assignment):
    if assignment.organization_id != organization(user):
        return False
    from app.services.business_hierarchy import is_business, can_manage_person
    if is_business(user):
        return can_manage_person(db, user, assignment.user_id)
    if is_manager(db, user):
        return True
    version = db.get(CourseVersion, assignment.course_version_id)
    return can_scope(db, user, 'manager', group_id=assignment.group_id, user_id=assignment.user_id) or can_scope(db, user, 'reviewer', course_id=version.course_id, group_id=assignment.group_id, user_id=assignment.user_id)


def response_dict(db, row, assignment, *, include_key=False):
    assessment = assessment_for(db, assignment, row.assessment_id)
    result = {k: getattr(row, k) for k in ['id', 'assignment_id', 'assessment_id', 'attempt', 'revision', 'answer', 'status', 'submitted_at', 'points', 'feedback', 'updated_at', 'submission_key']}
    result['max_points'] = assessment['max_points']
    result['grading_metadata'] = {k: v for k, v in (row.grading_metadata or {}).items() if k in {'method', 'reason'}}
    if include_key and (row.grading_metadata or {}).get('ai_proposal'):
        result['grading_metadata']['ai_proposal'] = row.grading_metadata['ai_proposal']
    result['can_retry'] = row.status == 'reviewed' and row.attempt < assignment.rules.get('max_attempts', 3) and (not assignment.due_at or utc(assignment.due_at) >= datetime.utcnow() or assignment.rules.get('allow_late_submission'))
    reveal = assignment.rules.get('reveal_answers', 'never')
    passed = row.status == 'reviewed' and row.points >= assessment['max_points'] * assignment.rules.get('pass_percent', 60) / 100
    final_attempt = row.status == 'reviewed' and row.attempt >= assignment.rules.get('max_attempts', 3)
    if include_key or (reveal == 'after_pass' and passed) or (reveal == 'after_final_attempt' and final_attempt):
        result['correct_answers'] = assessment.get('correct_answers', [])
    return result


def save_answer(db, user, assignment, key, payload, submit=False):
    ensure_learning_write(db, assignment.organization_id)
    assessment = assessment_for(db, assignment, key)
    if submit:
        existing = db.query(LearningResponse).filter_by(assignment_id=assignment.id, submission_key=payload.submission_key).first()
        if existing:
            if existing.assessment_id != key or existing.attempt != payload.attempt or existing.answer != payload.answer:
                raise HTTPException(409, 'Этот ключ отправки уже использован для другого ответа')
            return existing
    row = db.query(LearningResponse).filter_by(assignment_id=assignment.id, assessment_id=key, attempt=payload.attempt).first()
    if row and row.status != 'draft':
        raise HTTPException(409, 'Отправленный ответ неизменяем. Начните разрешённую новую попытку.')
    if (row.revision if row else 0) != payload.expected_revision:
        raise HTTPException(409, {'message': 'Ответ изменён на другом устройстве. Получите сохранённую версию и сравните текст.',
            'current_revision': row.revision if row else 0,
            'current_response': jsonable_encoder(response_dict(db, row, assignment)) if row else None})
    maximum = assignment.rules.get('max_attempts', 3)
    if payload.attempt > maximum:
        raise HTTPException(409, 'Лимит попыток исчерпан')
    if payload.attempt > 1:
        previous = db.query(LearningResponse).filter_by(assignment_id=assignment.id, assessment_id=key, attempt=payload.attempt - 1).first()
        if not previous or previous.status != 'reviewed':
            raise HTTPException(409, 'Дождитесь проверки предыдущей попытки')
    if submit and assignment.due_at and utc(assignment.due_at) < datetime.utcnow() and not assignment.rules.get('allow_late_submission', False):
        raise HTTPException(409, 'Срок отправки истёк. Черновик сохранён; обратитесь к ответственному за обучение.')
    if row is None:
        row = LearningResponse(assignment_id=assignment.id, assessment_id=key, attempt=payload.attempt, revision=1)
        db.add(row)
    else:
        row.revision += 1
    row.answer = deepcopy(payload.answer)
    if submit:
        if assessment['question_type'] == 'open':
            if not payload.answer.get('text', '').strip():
                raise HTTPException(422, 'Напишите ответ')
        else:
            selected = payload.answer.get('selected', [])
            if not selected or len(selected) != len(set(selected)) or any(x not in assessment['answers'] for x in selected):
                raise HTTPException(422, 'Выберите допустимые варианты ответа')
            if assessment['question_type'] == 'single' and len(selected) != 1:
                raise HTTPException(422, 'Выберите один вариант ответа')
        row.submission_key = payload.submission_key
        row.submitted_at = datetime.utcnow()
        row.status = 'pending_review'
        if assessment['question_type'] != 'open' and assessment['grading_mode'] == 'automatic':
            row.points = assessment['max_points'] if set(payload.answer.get('selected', [])) == set(assessment['correct_answers']) else 0
            row.status = 'reviewed'
            row.feedback = 'Ответ проверен автоматически по ключу опубликованной версии.'
            row.grading_metadata = {'method': 'exact_set'}
            db.flush()
            db.add(LearningGradeEvent(response_id=row.id, points=row.points, feedback=row.feedback, source='automatic'))
            event(db, assignment.organization_id, 'submission.graded', f'response:{row.id}:revision:{row.revision}', {'response_id': row.id, 'assignment_id': assignment.id, 'points': row.points, 'max_points': assessment['max_points']})
            from app.services.mobile_push import enqueue as enqueue_push
            enqueue_push(db, assignment, 'result', f'response:{row.id}:revision:{row.revision}')
        elif assessment['grading_mode'] == 'ai_assisted':
            # AI cannot silently decide a grade. A separate auditable proposal may
            # assist a scoped reviewer; the authoritative state stays in the queue.
            row.grading_metadata = {'method': 'manual', 'reason': 'Требуется подтверждение проверяющего'}
        audit(db, user, 'learning.answer_submitted', 'assignment', assignment.id, organization_id=assignment.organization_id, assessment_id=key, attempt=payload.attempt)
    db.flush()
    if submit and row.status == 'pending_review':
        from app.services.notifications import review_requested
        review_requested(db, assignment, row)
    update_completion(db, assignment)
    return row


def create_assignment(db, *, org_id, version_id, user_id, actor_id, rules, rules_revision, due_at=None, group_id=None, cycle_key='initial', prerequisite_id=None):
    ensure_learning_write(db, org_id)
    existing = db.query(LearningAssignment).filter_by(organization_id=org_id, course_version_id=version_id, user_id=user_id, cycle_key=cycle_key).first()
    if existing:
        return existing, False
    row = LearningAssignment(organization_id=org_id, course_version_id=version_id, user_id=user_id, assigned_by=actor_id,
                             rules=deepcopy(rules), rules_revision=rules_revision, due_at=utc(due_at), group_id=group_id, cycle_key=cycle_key,
                             prerequisite_id=prerequisite_id)
    db.add(row)
    db.flush()
    event(db, org_id, 'assignment.created', f'assignment:{row.id}:created', {'assignment_id': row.id, 'user_id': user_id, 'course_version_id': version_id})
    from app.services.mobile_push import enqueue as enqueue_push
    enqueue_push(db, row, 'assignment', f'assignment:{row.id}:created')
    return row, True


def assign_from_integration(db, org_id, version_id, user_id, actor_id, cycle_key='initial'):
    from app.services.organization_access import lock_org
    org = lock_org(db, org_id)
    if not org.is_active:
        raise HTTPException(403, 'Организация приостановлена')
    member(db, org_id, user_id)
    version = db.get(CourseVersion, version_id)
    course = db.get(Course, version.course_id) if version else None
    if not course or course.organization_id != org_id or course.is_deleted:
        raise HTTPException(404, 'Опубликованный курс не найден в организации интеграции')
    snapshot_for(db, version_id)
    config = settings_for(db, org_id)
    if config.features.get('learning') is False:
        raise HTTPException(403, 'Обучение не включено в поставку')
    if not cycle_key or len(cycle_key) > 64:
        raise HTTPException(422, 'Некорректный ключ назначения')
    return create_assignment(db, org_id=org_id, version_id=version_id, user_id=user_id, actor_id=actor_id,
        rules=LearningRules.model_validate(config.rules).model_dump(), rules_revision=config.revision, cycle_key=cycle_key)


def revoke_from_integration(db, org_id, assignment_id, actor_id):
    from app.services.organization_access import lock_org
    lock_org(db, org_id)
    row = db.query(LearningAssignment).filter_by(id=assignment_id, organization_id=org_id).first()
    if not row:
        raise HTTPException(404, 'Назначение не найдено')
    if row.state != 'revoked':
        row.state, row.revoked_at = 'revoked', datetime.utcnow()
        audit(db, db.get(User, actor_id), 'learning.assignment_revoked', 'assignment', row.id, organization_id=org_id)
        event(db, org_id, 'assignment.revoked', f'assignment:{row.id}:revoked', {'assignment_id': row.id})
    return row
