from sqlalchemy.orm import Session
from app.models.module import Module
from app.models.course import Course
from typing import Optional, List

class ModuleRepository:
    @staticmethod
    def add_module(db: Session, course_id: int, module_create) -> Module:
        """
        Создает новый модуль для курса с заданным course_id.
        """
        # Проверяем, существует ли курс с данным course_id
        course = db.query(Course).filter(Course.id == course_id, Course.is_deleted == False).first()
        if not course:
            raise ValueError("Course not found")

        new_module = Module(title=module_create.title, course_id=course_id)
        db.add(new_module)
        db.commit()
        db.refresh(new_module)
        return new_module

    @staticmethod
    def list_modules(db: Session, course_id: int) -> List[Module]:
        """
        Возвращает список всех модулей для курса с указанным course_id.
        """
        # Проверяем, существует ли курс с данным course_id
        course = db.query(Course).filter(Course.id == course_id, Course.is_deleted == False).first()
        if not course:
            raise ValueError("Course not found")
        return db.query(Module).filter(Module.course_id == course_id, Module.is_deleted == False).all()

    @staticmethod
    def get_by_id(db: Session, module_id: int) -> Optional[Module]:
        """
        Возвращает данные модуля по его ID.
        """
        return db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()

    @staticmethod
    def update_module(db: Session, mod: Module, update_data: dict) -> Module:
        """
        Обновляет данные модуля по его ID.
        """
        if "title" in update_data:
            mod.title = update_data["title"]
        db.commit()
        db.refresh(mod)
        return mod

    @staticmethod
    def delete_module(db: Session, mod: Module):
        """
        Удаляет модуль по его ID.
        """
        mod.is_deleted = True
        # db.delete(mod)
        db.commit()
