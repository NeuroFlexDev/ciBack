from sqlalchemy.orm import Session
from app.models.feedback import Feedback
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.course import Course
from app.schemas.feedback import FeedbackInput

class FeedbackRepository:
    @staticmethod
    def save_feedback(db: Session, feedback_input: FeedbackInput, user_id: int):
        lesson = (
            db.query(Lesson)
            .join(Module, Lesson.module_id == Module.id)
            .join(Course, Module.course_id == Course.id)
            .filter(
                Lesson.id == feedback_input.lesson_id,
                Lesson.is_deleted.is_(False),
                Course.owner_id == user_id,
            )
            .first()
        )
        if not lesson:
            raise ValueError("❌ Урок не найден")
        fb = Feedback(**feedback_input.model_dump(), author_id=user_id)
        db.add(fb)
        db.commit()
        db.refresh(fb)
        return fb
