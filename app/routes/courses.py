from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database.db import get_db
from app.models.course import Course

router = APIRouter()

# Pydantic-схема для JSON-запроса
class CourseCreate(BaseModel):
    title: str
    description: str
    level: str
    language: str

@router.post("/courses/")
async def create_course(course: CourseCreate, db: Session = Depends(get_db)):
    new_course = Course(
        name=course.title,
        description=course.description,
        level=course.level,
        language=course.language
    )
    db.add(new_course)
    db.commit()
    db.refresh(new_course)

    return {"message": "Course created", "course": new_course}
