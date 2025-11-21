from sqlalchemy.orm import Session
from app.models.feedback import Feedback
from app.models.lesson import Lesson

class FeedbackRepository:
    @staticmethod
    def save_feedback(db: Session, feedback_input: FeedbackInput):
        lesson = db.query(Lesson).filter(Lesson.id == feedback_input.lesson_id, Lesson.is_deleted == False).first()
        if not lesson:
            raise ValueError("❌ Урок не найден")
        fb = Feedback(**feedback_input.dict())
        db.add(fb)
        db.commit()
        return fb
