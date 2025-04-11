import logging
from app.database.db import get_db_session
from app.models.theory import Theory
from app.models.lesson import Lesson
from app.models.course import Course
from app.services.generation_service import generate_from_prompt
from app.services.external_sources import aggregated_search
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def get_course_agent():
    def improve_theory(lesson_id: int, goal: str) -> str:
        session: Session = get_db_session()
        
        theory = session.query(Theory).filter(Theory.lesson_id == lesson_id).first()
        lesson = session.query(Lesson).filter(Lesson.id == lesson_id).first()
        course = session.query(Course).filter(Course.id == lesson.module.course_id).first()

        if not theory or not lesson or not course:
            raise ValueError("Невозможно найти необходимые данные по lesson_id")

        external_context = aggregated_search(lesson.title)
        context_str = "\n\n".join(external_context)

        result = generate_from_prompt(
            "theory_improve_prompt.j2",
            course_title=course.name,
            course_description=course.description,
            lesson_title=lesson.title,
            original_theory=theory.content,
            goal=goal,
            external_context=context_str,
        )

        return result.get("improved_theory", "")
    
    return {"improve_theory": improve_theory}
