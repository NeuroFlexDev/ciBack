from sqlalchemy.orm import Session

from app.models.course import Course
from app.services.course_access import course_access_filter
from app.models.domain_enums import CourseStatus


class CourseDraftRepository:
    @staticmethod
    def create(db: Session, owner_id: int) -> Course:
        from app.services.organization_access import check_course_limit
        check_course_limit(db, owner_id)
        draft = Course(owner_id=owner_id, status=CourseStatus.DRAFT.value)
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft

    @staticmethod
    def list_for_owner(db: Session, owner_id: int) -> list[Course]:
        return (
            db.query(Course)
            .filter(
                course_access_filter(owner_id),
                Course.status == CourseStatus.DRAFT.value,
                Course.is_deleted.is_(False),
            )
            .order_by(Course.created_at.desc(), Course.id.desc())
            .all()
        )
