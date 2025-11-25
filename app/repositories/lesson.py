from sqlalchemy.orm import Session
from app.models.lesson import Lesson
from app.models.module import Module
from typing import Optional, List

class LessonRepository:
    @staticmethod
    def add_lesson(db: Session, course_id: int, module_id: int, lesson_create) -> Lesson:
        """
        Добавляет новый урок к модулю конкретного курса.
        Проверяет, что модуль существует и принадлежит указанному курсу.
        """
        module = db.query(Module).filter(Module.id == module_id, Module.course_id == course_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found for given course")

        new_lesson = Lesson(
            title=lesson_create.title,
            description=lesson_create.description,
            module_id=module.id,
        )
        db.add(new_lesson)
        db.commit()
        db.refresh(new_lesson)
        return new_lesson

    @staticmethod
    def get_lessons(db: Session, course_id: int, module_id: int) -> List[Lesson]:
        """
        Возвращает список всех уроков для модуля конкретного курса.
        """
        module = db.query(Module).filter(Module.id == module_id, Module.course_id == course_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found for given course")
        return module.lessons

    @staticmethod
    def get_lesson(db: Session, lesson_id: int) -> Optional[Lesson]:
        """
        Возвращает данные урока по его ID.
        """
        return db.query(Lesson).filter(Lesson.id == lesson_id, Lesson.is_deleted == False).first()

    @staticmethod
    def update_lesson(db: Session, lesson: Lesson, update_data: dict) -> Lesson:
        """
        Обновляет данные урока по указанному ID.
        """
        if "title" in update_data:
            lesson.title = update_data["title"]
        if "description" in update_data:
            lesson.description = update_data["description"]
        db.commit()
        db.refresh(lesson)
        return lesson

    @staticmethod
    def delete_lesson(db: Session, lesson: Lesson):
        """
        Удаляет урок по его ID.
        """
        lesson.is_deleted = True
        # db.delete(lesson)
        db.commit()
