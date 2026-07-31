from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.course_generation_settings import CourseGenerationSettings


class CourseGenerationSettingsRepository:
    @staticmethod
    def get_owned_course(
        db: Session, course_id: int, owner_id: int, *, for_update: bool = False
    ) -> Course | None:
        query = db.query(Course).filter(
            Course.id == course_id,
            Course.owner_id == owner_id,
            Course.is_deleted.is_(False),
        )
        if for_update:
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def get_for_course(
        db: Session, course_id: int, *, include_deleted: bool = False
    ) -> CourseGenerationSettings | None:
        query = db.query(CourseGenerationSettings).filter(
            CourseGenerationSettings.course_id == course_id
        )
        if not include_deleted:
            query = query.filter(CourseGenerationSettings.is_deleted.is_(False))
        return query.first()
