# app/services/feedback_service.py

from app.models.feedback import Feedback


def get_feedback_summary(lesson_id: int, db) -> str:
    feedbacks = db.query(Feedback).filter(Feedback.lesson_id == lesson_id).all()
    if not feedbacks:
        return ""

    comments = [f"- {f.comment}" for f in feedbacks if f.comment]
    avg_rating = round(
        sum(f.rating for f in feedbacks if f.rating is not None) / max(1, len(feedbacks)),
        2,
    )

    summary = f"""Средняя оценка: {avg_rating}/5.
Отзывы:
{chr(10).join(comments[:5])}"""

    return summary
