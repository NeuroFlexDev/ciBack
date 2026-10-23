"""Fresh, bounded evidence from the actor's library and assigned published versions.

No assessment keys, unrelated tenants, private colleague drafts, revoked or locked
assignments enter a prompt. References are resolved by the server, never by a model.
"""
from dataclasses import dataclass, field
import json
import re
from fastapi import HTTPException
from sqlalchemy import or_
from app.ai.evidence import rank_sources, terms
from app.models.course import Course
from app.models.document import Document, DocumentChunk
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.theory import Theory
from app.models.learning import LearningAssignment
from app.services.course_access import course_access_filter


@dataclass
class AssistantContext:
    catalog: list = field(default_factory=list)
    passages: list = field(default_factory=list)
    course_ids: set = field(default_factory=set)
    assignments: dict = field(default_factory=dict)

    def prompt(self):
        return json.dumps({'available_materials': self.catalog, 'excerpts': self.passages,
            'catalog_is_complete': len(self.catalog) < 50}, ensure_ascii=False)

    def references(self):
        return {row['ref']: row for row in [*self.catalog, *self.passages]}


def excerpt(content, query, size=1800):
    content = content or ''
    hits = [content.casefold().find(term) for term in set(terms(query))]
    hit = min((offset for offset in hits if offset >= 0), default=0)
    start = max(0, hit - 250)
    return content[start:start + size]


def _match(column, query):
    words = list(dict.fromkeys(terms(query)))[:12]
    return or_(*(column.icontains(word, autoescape=True) for word in words)) if words else column != ''


def build_context(db, user, query, *, course_id=None):
    result = AssistantContext()
    access = db.query(Course).filter(Course.is_deleted.is_(False), course_access_filter(user.id))
    if course_id is not None:
        access = access.filter(Course.id == course_id)
        if not access.first():
            raise HTTPException(404, 'Материал не найден')
    # Titles are navigation context, not proof of what a document requires.
    courses = access.order_by(_match(Course.name, query).desc(), Course.updated_at.desc(), Course.id.desc()).limit(50).all()
    for course in courses:
        result.course_ids.add(course.id)
        result.catalog.append({'ref': f'course:{course.id}', 'course_id': course.id,
            'title': course.name or 'Без названия', 'description': (course.description or '')[:500],
            'status': course.publication_status, 'revision': course.content_revision,
            'position': course.business_position.name if course.business_position else None,
            'url': f'/courses/{course.id}/edit'})
    allowed = access.with_entities(Course.id).subquery()
    chunks = db.query(DocumentChunk, Document).join(Document, Document.id == DocumentChunk.document_id).filter(
        Document.course_id.in_(db.query(allowed.c.id)), Document.is_deleted.is_(False), Document.is_current.is_(True),
        Document.status == 'indexed', DocumentChunk.is_deleted.is_(False), DocumentChunk.document_version == Document.version)
    # SQLite's built-in lower() does not case-fold Cyrillic. Keep its bounded
    # candidate scan broad; rank_sources applies Unicode matching in Python.
    sql_search = db.bind.dialect.name != "sqlite" or query.isascii()
    if course_id is None and sql_search:
        chunks = chunks.filter(_match(DocumentChunk.text, query))
    candidates = []
    for chunk, doc in chunks.order_by(DocumentChunk.id.desc()).limit(150):
        candidates.append({'ref': f'chunk:{chunk.id}', 'course_id': doc.course_id,
            'title': doc.original_filename, 'revision': doc.version, 'kind': 'source',
            'quote': excerpt(chunk.text, query), 'url': f'/courses/{doc.course_id}/edit', 'page': chunk.page})
    lessons = db.query(Lesson, Theory, Module).join(Module, Module.id == Lesson.module_id).join(Theory, Theory.lesson_id == Lesson.id).filter(
        Module.course_id.in_(db.query(allowed.c.id)), Module.is_deleted.is_(False), Lesson.is_deleted.is_(False), Theory.is_deleted.is_(False))
    if course_id is None and sql_search:
        lessons = lessons.filter(or_(_match(Theory.content, query), _match(Lesson.title, query)))
    for lesson, theory, module in lessons.order_by(Lesson.id.desc()).limit(100):
        candidates.append({'ref': f'lesson:{lesson.id}', 'course_id': module.course_id,
            'title': lesson.title, 'revision': lesson.revision, 'kind': 'authored_content',
            'quote': excerpt(theory.content, query), 'url': f'/courses/{module.course_id}/edit?lesson={lesson.id}'})
    # Learners use their frozen assignment, never today's author draft or test keys.
    if course_id is None and user.organization_id:
        from app.services.learner_assistant_service import assignment_context
        rows = db.query(LearningAssignment).filter_by(user_id=user.id, organization_id=user.organization_id, state='active').order_by(LearningAssignment.id.desc()).limit(30)
        for assignment in rows:
            try:
                current, snapshot = assignment_context(db, user, assignment.id)
            except HTTPException as exc:
                if exc.status_code in {403, 404, 409}:
                    continue
                raise
            result.assignments[current.id] = current.course_version_id
            title = snapshot.get('course', {}).get('title', 'Назначенный материал')
            result.catalog.append({'ref': f'assignment:{current.id}', 'title': title,
                'status': 'assigned', 'url': f'/learning/{current.id}'})
            for module in snapshot.get('modules', []):
                for lesson in module.get('lessons', []):
                    candidates.append({'ref': f'assigned:{current.id}:{lesson["id"]}', 'title': lesson['title'],
                        'kind': 'assigned_published_content', 'quote': excerpt(lesson.get('content', ''), query),
                        'url': f'/learning/{current.id}?item=lesson:{lesson["id"]}'})
    result.catalog = result.catalog[:50]
    ranked = rank_sources(query, candidates, limit=8)
    result.passages = ranked or (candidates[:4] if course_id else [])
    result.course_ids.update(row['course_id'] for row in result.passages if 'course_id' in row)
    return result


def verify_context(db, user_id, stamp, context):
    from app.services.ai_access_boundary import author_result_access
    from app.services.learner_assistant_service import assignment_context
    user = author_result_access(db, user_id, stamp, None, 'chat')
    for course_id in context.course_ids:
        author_result_access(db, user_id, stamp, course_id, 'chat')
    for assignment_id, version_id in context.assignments.items():
        assignment, _ = assignment_context(db, user, assignment_id)
        if assignment.course_version_id != version_id:
            raise HTTPException(403, 'Назначение изменилось во время ответа')


REFERENCE = re.compile(r'\[((?:course|lesson|chunk|assignment):\d+|assigned:\d+:\d+)\]')


def resolve_references(text, context):
    refs = context.references()
    if set(REFERENCE.findall(text)) - refs.keys():
        raise ValueError('Unknown material reference')
    def link(match):
        ref = refs[match[1]]
        label = re.sub(r'[\[\]\\\r\n]', '', ref['title'])[:100]
        return f"[{label}]({ref['url']})"
    return REFERENCE.sub(link, text)


def local_answer(query, context):
    matching = rank_sources(query, context.passages, limit=3)
    if matching:
        parts = ['Сейчас отвечаю по сохранённым материалам. Подходящие выдержки:']
        for row in matching:
            parts += ['\n'.join('> ' + line for line in row['quote'][:1000].splitlines()) + '\n[' + row['ref'] + ']']
    elif context.catalog:
        parts = ['Сейчас доступен локальный режим. Можно открыть эти материалы и уточнить вопрос:',
            '\n'.join('- [' + row['ref'] + ']' for row in context.catalog[:8])]
    else:
        parts = ['Сейчас доступен локальный режим. В вашем пространстве пока нет доступных материалов для ответа. Добавьте материалы или откройте назначенный регламент.']
    return {'text': resolve_references('\n\n'.join(parts), context), 'provider': 'local', 'model': 'local/extractive',
        'degraded': True, 'usage_reported': False, 'usage': {'input_tokens': 0, 'output_tokens': 0}}
