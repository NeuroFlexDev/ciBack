from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.lesson_version import LessonVersion
from app.models.module_version import ModuleVersion
from app.models.test_version import TestVersion
from app.repositories.course_publication import CoursePublicationRepository


class CoursePublicationService:
    @staticmethod
    def prepare_for_edit(course: Course) -> None:
        """Open a new working revision before the first edit after publication."""
        if course.publication_status == "published":
            course.publication_status = "draft"
            course.published_at = None
            course.content_revision += 1

    @staticmethod
    def _validate(course: Course, db: Session) -> tuple[list, list]:
        if course.status != "ready":
            raise HTTPException(status_code=409, detail="Only a ready course can be published")
        if CoursePublicationRepository.active_generation(db, course.id):
            raise HTTPException(status_code=409, detail="Course generation is still active")
        if not (course.name or "").strip():
            raise HTTPException(status_code=422, detail="Course title is required")
        modules = sorted(
            (item for item in course.modules if not item.is_deleted),
            key=lambda item: (item.position, item.id),
        )
        if not modules:
            raise HTTPException(status_code=422, detail="Course must contain an active module")
        lessons = [
            lesson
            for module in modules
            for lesson in sorted(
                (item for item in module.lessons if not item.is_deleted),
                key=lambda item: (item.position, item.id),
            )
        ]
        if not lessons:
            raise HTTPException(status_code=422, detail="Course must contain an active lesson")
        if any(not (module.title or "").strip() for module in modules):
            raise HTTPException(status_code=422, detail="Every module must have a title")
        if any(not (lesson.title or "").strip() for lesson in lessons):
            raise HTTPException(status_code=422, detail="Every lesson must have a title")
        if any(
            lesson.theory is None
            or lesson.theory.is_deleted
            or not (lesson.theory.content or "").strip()
            for lesson in lessons
        ):
            raise HTTPException(status_code=422, detail="Every lesson must have content")
        return modules, lessons

    @staticmethod
    def _snapshot(db: Session, course: Course, owner_id: int, published_at: datetime, modules: list) -> CourseVersion:
        version = CourseVersion(
            course_id=course.id,
            revision=course.content_revision,
            publication_status="published",
            published_at=published_at,
            created_by=owner_id,
            name=course.name,
            description=course.description,
            level=course.level,
            language=course.language,
        )
        db.add(version)
        db.flush()
        for module in modules:
            module_version = ModuleVersion(
                course_version_id=version.id,
                module_id=module.id,
                revision=module.revision,
                title=module.title,
                description=module.description,
                position=module.position,
                deleted=False,
                created_by=owner_id,
            )
            db.add(module_version)
            db.flush()
            for lesson in sorted((item for item in module.lessons if not item.is_deleted), key=lambda item: (item.position, item.id)):
                db.add(LessonVersion(
                    module_version_id=module_version.id,
                    lesson_id=lesson.id,
                    revision=lesson.revision,
                    title=lesson.title,
                    description=lesson.description,
                    content=lesson.theory.content,
                    position=lesson.position,
                    deleted=False,
                    created_by=owner_id,
                ))
            for test in sorted((item for item in module.tests if not item.is_deleted), key=lambda item: (item.position, item.id)):
                db.add(TestVersion(
                    course_version_id=version.id,
                    module_version_id=module_version.id,
                    test_id=test.id,
                    revision=test.revision,
                    assessment_scope=test.assessment_scope,
                    module_id=test.module_id,
                    course_id=test.course_id,
                    question=test.question,
                    answers=test.answers or "[]",
                    correct_answer=test.correct_answer,
                    position=test.position,
                    deleted=False,
                    created_by=owner_id,
                ))
        for test in sorted((item for item in course.final_tests if not item.is_deleted), key=lambda item: (item.position, item.id)):
            db.add(TestVersion(
                course_version_id=version.id,
                test_id=test.id,
                revision=test.revision,
                assessment_scope=test.assessment_scope,
                module_id=None,
                course_id=course.id,
                question=test.question,
                answers=test.answers or "[]",
                correct_answer=test.correct_answer,
                position=test.position,
                deleted=False,
                created_by=owner_id,
            ))
        return version

    @staticmethod
    def publish(db: Session, course_id: int, owner_id: int) -> dict:
        try:
            course = CoursePublicationRepository.owned_course(db, course_id, owner_id, lock=True)
            if course is None:
                raise HTTPException(status_code=404, detail="Course not found")
            if course.publication_status == "published":
                return {
                    "id": course.id,
                    "publication_status": "published",
                    "published_at": course.published_at,
                    "revision": course.content_revision,
                }
            modules, _ = CoursePublicationService._validate(course, db)
            published_at = datetime.utcnow()
            CoursePublicationService._snapshot(db, course, owner_id, published_at, modules)
            course.publication_status = "published"
            course.published_at = published_at
            db.commit()
            db.refresh(course)
            return {
                "id": course.id,
                "publication_status": "published",
                "published_at": course.published_at,
                "revision": course.content_revision,
            }
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def list_courses(db: Session, owner_id: int, publication_status: str | None, limit: int, offset: int):
        rows, total = CoursePublicationRepository.list_owned(db, owner_id, publication_status, limit, offset)
        items = [{
            "id": course.id,
            "title": course.name,
            "description": course.description,
            "level": int(course.level) if str(course.level).isdigit() else 1,
            "language": int(course.language) if str(course.language).isdigit() else 1,
            "publication_status": course.publication_status,
            "module_count": module_count,
            "lesson_count": lesson_count,
            "updated_at": course.updated_at,
            "published_at": course.published_at,
        } for course, module_count, lesson_count in rows]
        return items, total
