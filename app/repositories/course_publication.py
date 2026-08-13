from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.course import Course
from app.models.generation_run import GenerationRun
from app.models.lesson import Lesson
from app.models.module import Module


class CoursePublicationRepository:
    @staticmethod
    def owned_course(db: Session, course_id: int, owner_id: int, *, lock: bool = False) -> Course | None:
        query = db.query(Course).options(
            selectinload(Course.modules).selectinload(Module.lessons).selectinload(Lesson.theory),
            selectinload(Course.modules).selectinload(Module.tests),
            selectinload(Course.final_tests),
        ).filter(Course.id == course_id, Course.owner_id == owner_id, Course.is_deleted.is_(False))
        return (query.with_for_update() if lock else query).first()

    @staticmethod
    def active_generation(db: Session, course_id: int) -> bool:
        return db.query(GenerationRun.id).filter(
            GenerationRun.course_id == course_id,
            GenerationRun.status.in_(("queued", "running")),
            GenerationRun.is_deleted.is_(False),
        ).first() is not None

    @staticmethod
    def list_owned(db: Session, owner_id: int, publication_status: str | None, limit: int, offset: int):
        module_counts = db.query(Module.course_id.label("course_id"), func.count(Module.id).label("module_count")).filter(Module.is_deleted.is_(False)).group_by(Module.course_id).subquery()
        lesson_counts = db.query(Module.course_id.label("course_id"), func.count(Lesson.id).label("lesson_count")).join(Lesson, Lesson.module_id == Module.id).filter(Module.is_deleted.is_(False), Lesson.is_deleted.is_(False)).group_by(Module.course_id).subquery()
        query = db.query(Course, func.coalesce(module_counts.c.module_count, 0), func.coalesce(lesson_counts.c.lesson_count, 0)).outerjoin(module_counts, module_counts.c.course_id == Course.id).outerjoin(lesson_counts, lesson_counts.c.course_id == Course.id).filter(Course.owner_id == owner_id, Course.is_deleted.is_(False), Course.status != "draft")
        if publication_status:
            query = query.filter(Course.publication_status == publication_status)
        total = query.count()
        rows = query.order_by(Course.updated_at.desc(), Course.id.desc()).offset(offset).limit(limit).all()
        return rows, total
