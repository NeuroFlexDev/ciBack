from sqlalchemy.orm import Session

from app.models.course import Course
from app.repositories.course_draft import CourseDraftRepository


class CourseDraftService:
    @staticmethod
    def create(db: Session, owner_id: int) -> Course:
        return CourseDraftRepository.create(db, owner_id)

    @staticmethod
    def list_for_owner(db: Session, owner_id: int) -> list[Course]:
        return CourseDraftRepository.list_for_owner(db, owner_id)
