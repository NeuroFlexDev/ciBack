from sqlalchemy.orm import Session

from app.models.feedback import Feedback
from app.models.lesson import Lesson
from app.schemas.feedback import FeedbackCreate


class FeedbackRepository:
    @staticmethod
    def create_feedback(db: Session, payload: FeedbackCreate, *, author_id: int) -> Feedback:
        lesson = (
            db.query(Lesson)
            .filter(Lesson.id == payload.lesson_id, Lesson.is_deleted == False)
            .first()
        )
        if not lesson:
            raise ValueError("Lesson not found")

        feedback = Feedback(
            lesson_id=payload.lesson_id,
            author_id=author_id,
            type=payload.type,
            comment=payload.comment,
            rating=payload.rating,
        )
        db.add(feedback)
        db.commit()
        db.refresh(feedback)
        return feedback

    @staticmethod
    def list_for_lesson(db: Session, lesson_id: int) -> list[Feedback]:
        lesson = db.query(Lesson).filter(Lesson.id == lesson_id, Lesson.is_deleted == False).first()
        if not lesson:
            raise ValueError("Lesson not found")

        return (
            db.query(Feedback)
            .filter(Feedback.lesson_id == lesson_id, Feedback.is_deleted == False)
            .order_by(Feedback.created_at.desc(), Feedback.id.desc())
            .all()
        )
