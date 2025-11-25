from sqlalchemy.orm import Session
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from typing import Optional

class UploadRepository:
    @staticmethod
    def get_course(db: Session, course_id: int) -> Optional[Course]:
        return db.query(Course).filter(Course.id == course_id, Course.is_deleted == False).first()

    @staticmethod
    def get_lesson(db: Session, lesson_id: int) -> Optional[Lesson]:
        return db.query(Lesson).filter(Lesson.id == lesson_id, Lesson.is_deleted == False).first()

    @staticmethod
    def get_theory(db: Session, lesson_id: int) -> Optional[Theory]:
        return db.query(Theory).filter(Theory.lesson_id == lesson_id, Theory.is_deleted == False).first()
