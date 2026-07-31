from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.domain_enums import CourseStatus


class CourseDraftRepository:
    @staticmethod
    def create(db: Session, owner_id: int) -> Course:
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
                Course.owner_id == owner_id,
                Course.status == CourseStatus.DRAFT.value,
                Course.is_deleted.is_(False),
            )
            .order_by(Course.created_at.desc(), Course.id.desc())
            .all()
        )
