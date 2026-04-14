import logging
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, ConfigDict
from app.database.db import get_db
from app.models.course import Course
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.test import Test
from app.models.task import Task
from app.models.user import User
from app.security import get_current_user

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

    model_config = ConfigDict(from_attributes=True)


class LessonPayload(BaseModel):
    lesson: str
    description: str = ""


class TestPayload(BaseModel):
    test: str
    description: str = ""


class TaskPayload(BaseModel):
    name: str
    description: str = ""


class ModulePayload(BaseModel):
    title: str
    lessons: list[LessonPayload] = Field(default_factory=list)
    tests: list[TestPayload] = Field(default_factory=list)
    tasks: list[TaskPayload] = Field(default_factory=list)


class ModulesSaveRequest(BaseModel):
    modules: list[ModulePayload] = Field(default_factory=list)


def parse_course_enum(value: object) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else 1
    except (TypeError, ValueError):
        return 1

@router.post("/courses/", summary="Создание курса", response_model=CourseResponse)
def create_course(
    course: CourseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Создает курс в базе данных.
    """
    try:
        logger.info("Получен запрос на создание курса: %s", course.json())
        new_course = Course(
            name=course.title,
            description=course.description,
            level=course.level,
            language=course.language,
            owner_id=current_user.id,
        )
        db.add(new_course)
        db.commit()
        db.refresh(new_course)
        logger.info("Курс успешно создан: ID=%s, Название=%s", new_course.id, new_course.name)
        return CourseResponse(
            id=new_course.id,
            title=new_course.name,
            description=new_course.description,
            level=parse_course_enum(new_course.level),
            language=parse_course_enum(new_course.language),
        )
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при создании курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при создании курса")

@router.get("/courses/", summary="Получение всех курсов", response_model=list[CourseResponse])
def get_all_courses(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Возвращает список всех курсов, сохраненных в базе данных.
    """
    try:
        courses = db.query(Course).filter(Course.owner_id == current_user.id).all()
        return [
            CourseResponse(
                id=course.id,
                title=course.name,
                description=course.description,
                level=parse_course_enum(course.level),
                language=parse_course_enum(course.language),
            )
            for course in courses
        ]
    except Exception as e:
        logger.error("Ошибка при получении курсов: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при получении курсов")

@router.put("/courses/{course_id}", summary="Обновление курса", response_model=CourseResponse)
def update_course(
    course_id: int,
    course_update: CourseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Обновляет данные курса по указанному ID.
    """
    try:
        course = (
            db.query(Course)
            .filter(Course.id == course_id, Course.owner_id == current_user.id)
            .first()
        )
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
            level=parse_course_enum(course.level),
            language=parse_course_enum(course.language),
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error("Ошибка при обновлении курса: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка при обновлении курса")

@router.delete("/courses/{course_id}", summary="Удаление курса")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Удаляет курс по указанному ID.
    """
    try:
        course = (
            db.query(Course)
            .filter(Course.id == course_id, Course.owner_id == current_user.id)
            .first()
        )
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


@router.get("/courses/{course_id}/load_modules", summary="Получение полной структуры курса")
def load_modules(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = (
        db.query(Course)
        .filter(Course.id == course_id, Course.owner_id == current_user.id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Курс не найден")

    modules_payload = []
    for module in course.modules:
        tests_payload = []
        for test in module.tests:
            answers = json.loads(test.answers) if test.answers else []
            description = ""
            if answers or test.correct_answer:
                description = (
                    f"Варианты: {', '.join(answers)} "
                    f"(Правильный: {test.correct_answer})"
                ).strip()

            tests_payload.append(
                {
                    "test": test.question,
                    "description": description,
                }
            )

        modules_payload.append(
            {
                "title": module.title,
                "lessons": [
                    {
                        "lesson": lesson.title,
                        "description": lesson.description or "",
                    }
                    for lesson in module.lessons
                ],
                "tests": tests_payload,
                "tasks": [
                    {
                        "name": task.name,
                        "description": task.description or "",
                    }
                    for task in module.tasks
                ],
            }
        )

    return {"modules": modules_payload}


@router.post("/courses/{course_id}/save_modules", summary="Сохранение полной структуры курса")
def save_modules(
    course_id: int,
    payload: ModulesSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    course = (
        db.query(Course)
        .filter(Course.id == course_id, Course.owner_id == current_user.id)
        .first()
    )
    if not course:
        raise HTTPException(status_code=404, detail="Курс не найден")

    for module in list(course.modules):
        db.delete(module)

    db.flush()

    for module_payload in payload.modules:
        new_module = Module(title=module_payload.title, course_id=course_id)
        db.add(new_module)
        db.flush()

        for lesson_payload in module_payload.lessons:
            db.add(
                Lesson(
                    title=lesson_payload.lesson,
                    description=lesson_payload.description,
                    module_id=new_module.id,
                )
            )

        for test_payload in module_payload.tests:
            answers: list[str] = []
            correct_answer = ""
            description = test_payload.description
            if "Варианты:" in description and "(Правильный:" in description:
                parts = description.split("Варианты:", maxsplit=1)[1].split("(Правильный:", maxsplit=1)
                answers = [answer.strip() for answer in parts[0].split(",") if answer.strip()]
                correct_answer = parts[1].replace(")", "").strip()

            db.add(
                Test(
                    module_id=new_module.id,
                    question=test_payload.test,
                    answers=json.dumps(answers, ensure_ascii=False),
                    correct_answer=correct_answer,
                )
            )

        for task_payload in module_payload.tasks:
            db.add(
                Task(
                    module_id=new_module.id,
                    name=task_payload.name,
                    description=task_payload.description,
                )
            )

    db.commit()
    return {"message": "Модули успешно сохранены"}
