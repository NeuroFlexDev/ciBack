from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.course_structure import CourseStructure
from app.schemas.course_structure import CourseStructureCreate


class CourseStructureRepository:
    @staticmethod
    def create(db: Session, struct: CourseStructureCreate) -> CourseStructure:
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
        return (
            db.query(CourseStructure)
            .filter(CourseStructure.is_deleted == False)
            .order_by(CourseStructure.id.asc())
            .all()
        )

    @staticmethod
    def get_by_id(db: Session, cs_id: int) -> Optional[CourseStructure]:
        return (
            db.query(CourseStructure)
            .filter(CourseStructure.id == cs_id, CourseStructure.is_deleted == False)
            .first()
        )

    @staticmethod
    def update(db: Session, cs: CourseStructure, data: dict) -> CourseStructure:
        if "content_types" in data and data["content_types"] is not None:
            data["content_types"] = ",".join(data["content_types"])

        for field, value in data.items():
            setattr(cs, field, value)

        db.commit()
        db.refresh(cs)
        return cs

    @staticmethod
    def delete(db: Session, cs: CourseStructure) -> None:
        cs.is_deleted = True
        db.commit()
