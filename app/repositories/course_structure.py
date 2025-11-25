from sqlalchemy.orm import Session
from app.models.course_structure import CourseStructure
from typing import Optional, List

class CourseStructureRepository:
    @staticmethod
    def create(db: Session, struct) -> CourseStructure:
        # Создает новую структуру курса.
        new_struct = CourseStructure(
            sections=struct.sections,
            tests_per_section=struct.tests_per_section,
            lessons_per_section=struct.lessons_per_section,
            questions_per_test=struct.questions_per_test,
            final_test=struct.final_test,
            content_types=",".join(struct.content_types) if struct.content_types else "",
        )
        db.add(new_struct)
        db.commit()
        db.refresh(new_struct)
        return new_struct

    @staticmethod
    def list_all(db: Session) -> List[CourseStructure]:
        # Возвращает список всех структур курсов.
        return db.query(CourseStructure).all()

    @staticmethod
    def get_by_id(db: Session, cs_id: int) -> Optional[CourseStructure]:
        # Возвращает структуру курса по указанному ID.
        return db.query(CourseStructure).filter(CourseStructure.id == cs_id).first()

    @staticmethod
    def update(db: Session, cs: CourseStructure, data: dict) -> CourseStructure:
        # Обновляет структуру курса по указанному ID.
        if "content_types" in data and data["content_types"] is not None:
            data["content_types"] = ",".join(data["content_types"])
        for field, value in data.items():
            setattr(cs, field, value)
        db.commit()
        db.refresh(cs)
        return cs

    @staticmethod
    def delete(db: Session, cs: CourseStructure):
        # Удаляет структуру курса по указанному ID.
        cs.is_deleted = True
        # db.delete(cs)
        db.commit()
