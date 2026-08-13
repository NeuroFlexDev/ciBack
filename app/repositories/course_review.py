from sqlalchemy.orm import Session, selectinload

from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module


class CourseReviewRepository:
    @staticmethod
    def owned_course(db: Session, course_id: int, owner_id: int) -> Course | None:
        return (
            db.query(Course)
            .options(
                selectinload(Course.modules).selectinload(Module.lessons).selectinload(Lesson.theory),
                selectinload(Course.modules).selectinload(Module.tests),
                selectinload(Course.final_tests),
            )
            .filter(
                Course.id == course_id,
                Course.owner_id == owner_id,
                Course.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def owned_module(db: Session, module_id: int, owner_id: int) -> Module | None:
        return (
            db.query(Module)
            .join(Course, Module.course_id == Course.id)
            .options(selectinload(Module.lessons).selectinload(Lesson.theory), selectinload(Module.tests))
            .filter(
                Module.id == module_id,
                Module.is_deleted.is_(False),
                Course.owner_id == owner_id,
                Course.is_deleted.is_(False),
            )
            .first()
        )
