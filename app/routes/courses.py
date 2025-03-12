from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database.db import get_db
from app.models.course import Course

import logging  # Добавляем логирование

router = APIRouter()

# Настраиваем логирование (вывод в консоль + файл)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Pydantic-схема для запроса
class CourseCreate(BaseModel):
    title: str
    description: str
    level: int  # Было str, но в UI select передаёт id (число)
    language: int  # Было str, но в UI передаёт id (число)


@router.post("/courses/")
async def create_course(course: CourseCreate, db: Session = Depends(get_db)):
    """
    Создаёт курс в базе данных и логирует запрос
    """
    try:
        logging.info(f"Получен запрос: {course}")  # Логируем объект Pydantic
        logging.info(f"Как словарь: {course.dict()}")  # Логируем содержимое

        if not course.title or not course.description or not course.level or not course.language:
            logging.error("Некоторые поля пустые! Запрос был некорректным.")
            raise HTTPException(status_code=400, detail="Все поля (title, description, level, language) обязательны!")

        new_course = Course(
            name=course.title,
            description=course.description,
            level=course.level,
            language=course.language
        )
        db.add(new_course)
        db.commit()
        db.refresh(new_course)

        logging.info(f"Курс успешно создан: {new_course.id} - {new_course.name}")
        return {"message": "Course created", "course": new_course}

    except Exception as e:
        logging.error(f"Ошибка при создании курса: {str(e)}")
        raise HTTPException(status_code=500, detail="Ошибка при создании курса")
