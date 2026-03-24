from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.lesson import Lesson
from app.models.theory import Theory
from app.models.user import User
from app.schemas.agent import ImproveTheoryResponse
from app.services.access_control import ensure_can_manage_lesson
from app.services.external_sources import aggregated_search
from app.services.generation_service import generate_from_prompt


def improve_theory_for_lesson(
    db: Session,
    *,
    user: User,
    lesson_id: int,
    goal: str,
) -> ImproveTheoryResponse:
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id, Lesson.is_deleted == False).first()
    if lesson is None:
        raise HTTPException(404, "Lesson not found")
    ensure_can_manage_lesson(user, lesson)

    theory = db.query(Theory).filter(Theory.lesson_id == lesson_id, Theory.is_deleted == False).first()
    if theory is None:
        raise HTTPException(404, "Theory not found")
    if lesson.module is None or lesson.module.course is None:
        raise HTTPException(404, "Course not found")

    try:
        external_hits = aggregated_search(lesson.title)
    except Exception:
        external_hits = []
    external_context = "\n\n".join(external_hits)

    result = generate_from_prompt(
        "theory_improve_prompt.j2",
        course_title=lesson.module.course.name,
        course_description=lesson.module.course.description,
        lesson_title=lesson.title,
        original_theory=theory.content,
        goal=goal,
        external_context=external_context,
        include_external_context=False,
        use_feedback=True,
        db=db,
        lesson_id=lesson.id,
    )

    improved = result.get("improved_theory") or result.get("text") or theory.content
    return ImproveTheoryResponse(
        lesson_id=lesson_id,
        original=theory.content,
        improved=improved,
        used_external_context=bool(external_context.strip()),
    )
