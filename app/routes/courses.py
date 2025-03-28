import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from app.database.db import get_db
from app.models.course import Course

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic-модель для создания курса
class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Название курса")
    description: str = Field(..., min_length=1, description="Описание курса")
    level: int = Field(..., gt=0, description="ID уровня курса (целое число > 0)")
    language: int = Field(..., gt=0, description="ID языка курса (целое число > 0)")

# Pydantic-модель для обновления курса
class CourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, description="Новое название курса")
    description: str | None = Field(None, min_length=1, description="Новое описание курса")
    level: int | None = Field(None, gt=0, description="Новый ID уровня курса (целое число > 0)")
    language: int | None = Field(None, gt=0, description="Новый ID языка курса (целое число > 0)")

# Pydantic-модель для ответа
class CourseResponse(BaseModel):
    id: int
    title: str
    description: str
    level: int
    language: int

    class Config:
        orm_mode = True

@router.post("/courses/", summary="Создание курса", response_model=CourseResponse)
def create_course(course: CourseCreate, db: Session = Depends(get_db)):
    """
    Создает курс в базе данных.
    """
    try:
        logger.info("Получен запрос на создание курса: %s", course.json())
        new_course = Course(
            name=course.title,
            description=course.description,
            level=course.level,
            language=course.language
        )
        db.add(new_course)
        db.commit()
        db.refresh(new_course)
        logger.info("Курс успешно создан: ID=%s, Название=%s", new_course.id, new_course.name)
        return CourseResponse(
            id=new_course.id,
            title=new_course.name,
            description=new_course.description,
            level=new_course.level,
            language=new_course.language
        )
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при создании курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при создании курса")

@router.get("/courses/", summary="Получение всех курсов", response_model=list[CourseResponse])
def get_all_courses(db: Session = Depends(get_db)):
    """
    Возвращает список всех курсов, сохраненных в базе данных.
    """
    try:
        courses = db.query(Course).all()
        return [
            CourseResponse(
                id=course.id,
                title=course.name,
                description=course.description,
                level=course.level,
                language=course.language
            )
            for course in courses
        ]
    except Exception as e:
        logger.error("Ошибка при получении курсов: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при получении курсов")

@router.put("/courses/{course_id}", summary="Обновление курса", response_model=CourseResponse)
def update_course(course_id: int, course_update: CourseUpdate, db: Session = Depends(get_db)):
    """
    Обновляет данные курса по указанному ID.
    """
    try:
        course = db.query(Course).filter(Course.id == course_id).first()
        if not course:
            raise HTTPException(status_code=404, detail="Курс не найден")
        
        update_data = course_update.dict(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="Нет данных для обновления")
        
        for field, value in update_data.items():
            # Если поле 'title' передается, оно маппится на поле 'name' в модели Course
            if field == "title":
                setattr(course, "name", value)
            else:
                setattr(course, field, value)
        
        db.commit()
        db.refresh(course)
        logger.info("Курс обновлен: ID=%s, Обновленные данные: %s", course.id, update_data)
        return CourseResponse(
            id=course.id,
            title=course.name,
            description=course.description,
            level=course.level,
            language=course.language
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при обновлении курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при обновлении курса")

@router.delete("/courses/{course_id}", summary="Удаление курса")
def delete_course(course_id: int, db: Session = Depends(get_db)):
    """
    Удаляет курс по указанному ID.
    """
    try:
        course = db.query(Course).filter(Course.id == course_id).first()
        if not course:
            raise HTTPException(status_code=404, detail="Курс не найден")
        
        db.delete(course)
        db.commit()
        logger.info("Курс удален: ID=%s", course_id)
        return {"message": f"Курс с ID {course_id} успешно удален"}
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при удалении курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при удалении курса")
