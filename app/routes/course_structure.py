from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course_structure import CourseStructure

router = APIRouter()

class CourseStructureCreate(BaseModel):
    sections: int
    tests_per_section: int
    lessons_per_section: int
    questions_per_test: int
    final_test: bool
    content_types: list[str]

@router.post("/course-structure/")
def save_course_structure(struct: CourseStructureCreate, db: Session = Depends(get_db)):
    """
    Сохраняет структуру курса в БД (перезапишет, если уже есть).
    """
    existing = db.query(CourseStructure).first()
    if existing:
        db.delete(existing)
        db.commit()

    new_struct = CourseStructure(
        sections=struct.sections,
        tests_per_section=struct.tests_per_section,
        lessons_per_section=struct.lessons_per_section,
        questions_per_test=struct.questions_per_test,
        final_test=struct.final_test,
        content_types=",".join(struct.content_types) if struct.content_types else ""
    )
    db.add(new_struct)
    db.commit()
    db.refresh(new_struct)
    return {"message": "Structure saved", "id": new_struct.id}

@router.get("/course-structure/")
def get_course_structure(db: Session = Depends(get_db)):
    """
    Возвращает сохраненную структуру (или 404, если нет).
    """
    cs = db.query(CourseStructure).first()
    if not cs:
        raise HTTPException(404, "Структура курса не найдена")
    return {
        "id": cs.id,
        "sections": cs.sections,
        "tests_per_section": cs.tests_per_section,
        "lessons_per_section": cs.lessons_per_section,
        "questions_per_test": cs.questions_per_test,
        "final_test": cs.final_test,
        "content_types": cs.content_types.split(",") if cs.content_types else []
    }
