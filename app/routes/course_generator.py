import json
import traceback
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.theory import Theory
from app.models.test import Test
from app.models.task import Task
from app.models.course_structure import CourseStructure
from app.services.generation_service import generate_from_prompt
from app.services.embedding_service import embed_and_add

router = APIRouter()
logger = logging.getLogger(__name__)

class ModuleTitle(BaseModel):
    title: str

class LessonRequest(BaseModel):
    lesson_id: int

class ModuleLessonGenerationRequest(BaseModel):
    module_id: int
    module_title: str


def course_kwargs(course: Course, cs: CourseStructure = None):
    data = {
        "course_name": course.name,
        "course_description": course.description,
        "course_level": course.level,
    }
    if cs:
        data.update({
            "module_count": cs.sections,
            "lessons_per_section": cs.lessons_per_section,
        })
    return data


@router.get("/courses/{course_id}/generate_modules", summary="Генерация и сохранение модулей курса")
def generate_and_save_modules(course_id: int, cs_id: int = Query(...), db: Session = Depends(get_db)):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = db.query(CourseStructure).filter(CourseStructure.id == cs_id).first()
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    modules_data = generate_from_prompt("module_prompt.j2", **course_kwargs(course, cs))

    db.query(Module).filter(Module.course_id == course.id).delete()

    try:
        for mod in modules_data["modules"]:
            new_module = Module(course_id=course.id, title=mod["title"])
            db.add(new_module)
            db.flush()

            for lesson in mod.get("lessons", []):
                db.add(Lesson(module_id=new_module.id, title=lesson.get("title", "Без названия"), description=lesson.get("description", "")))

            for test in mod.get("tests", []):
                desc = test.get("description", "")
                if "Варианты:" in desc and "(Правильный:" in desc:
                    parts = desc.split("Варианты:")[1].split("(Правильный:")
                    answers = parts[0].strip().split(", ")
                    correct = parts[1].replace(")", "").strip()
                    db.add(Test(module_id=new_module.id, question=test["test"], answers=json.dumps(answers), correct_answer=correct))

            for task in mod.get("tasks", []):
                if "name" in task:
                    db.add(Task(module_id=new_module.id, name=task["name"], description=task.get("description", "")))

        db.commit()
        return {"message": "✅ Модули успешно сгенерированы и сохранены"}

    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"❌ Ошибка сохранения модулей: {str(e)}")


@router.post("/courses/{course_id}/generate_module_lessons", summary="Генерация уроков модуля с сохранением")
def generate_and_save_module_lessons(course_id: int, cs_id: int = Query(...), payload: ModuleLessonGenerationRequest = Depends(), db: Session = Depends(get_db)):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = db.query(CourseStructure).filter(CourseStructure.id == cs_id).first()
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    lessons_data = generate_from_prompt("module_lessons_prompt.j2", **course_kwargs(course, cs), module_title=payload.module_title)

    if "lessons" not in lessons_data:
        raise HTTPException(500, "❌ LLM не вернул ключ 'lessons'")

    module = db.query(Module).filter(Module.id == payload.module_id, Module.course_id == course_id).first()
    if not module:
        raise HTTPException(404, "❌ Модуль не найден")

    for lesson in module.lessons:
        db.delete(lesson)

    for lesson in lessons_data["lessons"]:
        db.add(Lesson(module_id=module.id, title=lesson.get("title", "Без названия"), description=lesson.get("description", "")))

    db.commit()
    return {"message": "✅ Уроки успешно сгенерированы и сохранены"}


@router.post("/courses/{course_id}/generate_lesson_content", summary="Генерация контента урока")
def generate_and_save_lesson_content(course_id: int, payload: LessonRequest, db: Session = Depends(get_db)):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first()
    if not lesson:
        raise HTTPException(404, "❌ Урок не найден")

    content_data = generate_from_prompt(
        "lesson_content_prompt.j2",
        **course_kwargs(course),
        lesson_title=lesson.title
    )

    existing = db.query(Theory).filter(Theory.lesson_id == lesson.id).first()
    if existing:
        existing.content = content_data.get("theory", "")
    else:
        theory = Theory(lesson_id=lesson.id, content=content_data.get("theory", ""))
        db.add(theory)

    db.query(Task).filter(Task.module_id == lesson.module_id).delete()
    db.query(Test).filter(Test.module_id == lesson.module_id).delete()

    for task in content_data.get("tasks", []):
        db.add(Task(
            module_id=lesson.module_id,
            name=task.get("name", "Задание"),
            description=task.get("description", "")
        ))

    for q in content_data.get("questions", []):
        db.add(Test(
            module_id=lesson.module_id,
            question=q.get("question", ""),
            answers=json.dumps(q.get("answers", [])),
            correct_answer=q.get("correct", "")
        ))

    db.commit()

    # ✅ Векторизация всего контента
    embed_and_add(lesson.id, "lesson", lesson.title)
    embed_and_add(lesson.id, "theory", content_data.get("theory", ""))

    for task in content_data.get("tasks", []):
        embed_and_add(lesson.id, "task", task.get("description", ""))

    for q in content_data.get("questions", []):
        embed_and_add(lesson.id, "test", q.get("question", ""))

    return {
        "message": "✅ Контент сгенерирован и сохранён (теория, задачи, тесты)",
        "questions": content_data.get("questions", []),
        "tasks": content_data.get("tasks", [])
    }
