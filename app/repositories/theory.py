from sqlalchemy.orm import Session
from app.models.lesson import Lesson
from app.models.theory import Theory
from typing import Optional
from app.schemas.theory import TheoryCreate, TheoryUpdate

class TheoryRepository:
    @staticmethod
    def create_theory(db: Session, payload: TheoryCreate) -> Theory:
        lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id, Lesson.is_deleted == False).first()
        if not lesson:
            raise ValueError("Урок не найден")
        existing = db.query(Theory).filter(Theory.lesson_id == payload.lesson_id, Theory.is_deleted == False).first()
        if existing:
            raise ValueError("Теория уже существует для этого урока")
        theory = Theory(lesson_id=payload.lesson_id, content=payload.content)
        db.add(theory)
        db.commit()
        db.refresh(theory)
        return theory

    @staticmethod
    def get_theory_by_lesson(db: Session, lesson_id: int) -> Optional[Theory]:
        return db.query(Theory).filter(Theory.lesson_id == lesson_id, Theory.is_deleted == False).first()

    @staticmethod
    def update_theory(db: Session, theory: Theory, payload: TheoryUpdate):
        theory.content = payload.content
        db.commit()
        db.refresh(theory)
        return theory

    @staticmethod
    def delete_theory(db: Session, theory: Theory):
        theory.is_deleted = True
        # db.delete(theory)
        db.commit()
