from sqlalchemy.orm import Session
from app.models.course import Course
from app.models.course_structure import CourseStructure
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory

class GenerationRepository:
    @staticmethod
    def get_course(db: Session, course_id: int) -> Course | None:
        return db.query(Course).filter(Course.is_deleted == False).filter_by(id=course_id).first()

    @staticmethod
    def get_structure(db: Session, cs_id: int) -> CourseStructure | None:
        return db.query(CourseStructure).filter(CourseStructure.is_deleted == False).filter_by(id=cs_id).first()

    @staticmethod
    def get_module(db: Session, module_id: int, course_id: int) -> Module | None:
        return db.query(Module).filter(Module.is_deleted == False).filter_by(id=module_id, course_id=course_id).first()

    @staticmethod
    def add_module(db: Session, course_id: int, title: str) -> Module:
        m = Module(course_id=course_id, title=title)
        db.add(m)
        db.flush()
        return m
    # аналогичный CRUD для lessons, tests, tasks
