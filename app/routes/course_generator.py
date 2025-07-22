# app/routes/generation.py
import json
import logging
import traceback
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.course_structure import CourseStructure
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from app.services.embedding_service import embed_and_add
from app.services.generation_service import generate_from_prompt

router = APIRouter(prefix="/courses", tags=["Generation"])
logger = logging.getLogger(__name__)

DEFAULT_ENGINE = "lc_giga"
DEFAULT_MODEL: str | None = None


# ---------- Schemas ----------
class LessonRequest(BaseModel):
    lesson_id: int


# ---------- Helpers ----------
def course_kwargs(course: Course, cs: CourseStructure | None = None) -> dict[str, Any]:
    data = {
        "course_name": course.name,
        "course_description": course.description,
        "course_level": course.level,
    }
    if cs:
        data.update(
            {
                "module_count": cs.sections,
                "lessons_per_section": cs.lessons_per_section,
            }
        )
    return data


def _llm_json(template: str, *, engine: str, model: str | None, **kwargs) -> dict[str, Any]:
    try:
        return generate_from_prompt(
            template_name=template,
            engine=engine,
            model_name=model,
            expect_json=True,
            include_external_context=False,
            use_feedback=False,
            **kwargs,
        )
    except Exception as e:
        logger.error("LLM error: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"Ошибка генерации LLM: {e}")


# ---------- Routes ----------
@router.get("/{course_id}/generate_modules", summary="Генерация и сохранение модулей курса")
def generate_and_save_modules(
    course_id: int,
    cs_id: int = Query(..., description="ID структуры курса"),
    engine: str = Query(DEFAULT_ENGINE),
    model: str | None = Query(DEFAULT_MODEL),
    db: Session = Depends(get_db),
):
    course = db.query(Course).filter_by(id=course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = db.query(CourseStructure).filter_by(id=cs_id).first()
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    data = _llm_json("module_prompt.j2", engine=engine, model=model, **course_kwargs(course, cs))

    modules = data.get("modules")
    if not isinstance(modules, list):
        raise HTTPException(500, "LLM не вернул корректный ключ 'modules'")

    db.query(Module).filter(Module.course_id == course.id).delete()

    try:
        for mod in modules:
            m = Module(course_id=course.id, title=mod.get("title") or "Без названия")
            db.add(m)
            db.flush()

            for lesson in mod.get("lessons", []):
                db.add(
                    Lesson(
                        module_id=m.id,
                        title=lesson.get("title", "Без названия"),
                        description=lesson.get("description", ""),
                    )
                )

            for test in mod.get("tests", []):
                question = test.get("test") or test.get("question") or ""
                answers = test.get("answers")
                correct = test.get("correct") or test.get("answer") or ""
                if answers is None:
                    desc = test.get("description", "")
                    try:
                        if "Варианты:" in desc and "(Правильный:" in desc:
                            parts = desc.split("Варианты:")[1].split("(Правильный:")
                            answers = parts[0].strip().split(", ")
                            correct = parts[1].replace(")", "").strip()
                    except Exception:
                        answers = []
                db.add(
                    Test(
                        module_id=m.id,
                        question=question,
                        answers=json.dumps(answers or []),
                        correct_answer=correct,
                    )
                )

            for task in mod.get("tasks", []):
                db.add(
                    Task(
                        module_id=m.id,
                        name=task.get("name") or "Задание",
                        description=task.get("description", ""),
                    )
                )

        db.commit()
        return {"message": "✅ Модули успешно сгенерированы и сохранены"}
    except Exception as e:
        db.rollback()
        logger.error("DB error: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"❌ Ошибка сохранения модулей: {e}")


@router.post(
    "/{course_id}/generate_module_lessons",
    summary="Генерация уроков модуля с сохранением",
)
def generate_and_save_module_lessons(
    course_id: int,
    cs_id: int = Query(...),
    module_id: int = Query(...),
    module_title: str = Query(...),
    engine: str = Query(DEFAULT_ENGINE),
    model: str | None = Query(DEFAULT_MODEL),
    db: Session = Depends(get_db),
):
    course = db.query(Course).filter_by(id=course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = db.query(CourseStructure).filter_by(id=cs_id).first()
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    module = db.query(Module).filter_by(id=module_id, course_id=course_id).first()
    if not module:
        raise HTTPException(404, "❌ Модуль не найден")

    payload = course_kwargs(course, cs)
    payload["module_title"] = module_title

    data = _llm_json("module_lessons_prompt.j2", engine=engine, model=model, **payload)

    lessons = data.get("lessons")
    if not isinstance(lessons, list):
        raise HTTPException(500, "❌ LLM не вернул ключ 'lessons'")

    for l in module.lessons:
        db.delete(l)

    for lesson in lessons:
        db.add(
            Lesson(
                module_id=module.id,
                title=lesson.get("title", "Без названия"),
                description=lesson.get("description", ""),
            )
        )

    db.commit()
    return {"message": "✅ Уроки успешно сгенерированы и сохранены"}


@router.post("/{course_id}/generate_lesson_content", summary="Генерация контента урока")
def generate_and_save_lesson_content(
    course_id: int,
    payload: LessonRequest,
    engine: str = Query(DEFAULT_ENGINE),
    model: str | None = Query(DEFAULT_MODEL),
    db: Session = Depends(get_db),
):
    course = db.query(Course).filter_by(id=course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    lesson = db.query(Lesson).filter_by(id=payload.lesson_id).first()
    if not lesson:
        raise HTTPException(404, "❌ Урок не найден")

    data = _llm_json(
        "lesson_content_prompt.j2",
        engine=engine,
        model=model,
        **course_kwargs(course),
        lesson_title=lesson.title,
    )

    theory_text = data.get("theory", "")
    existing = db.query(Theory).filter_by(lesson_id=lesson.id).first()
    if existing:
        existing.content = theory_text
    else:
        db.add(Theory(lesson_id=lesson.id, content=theory_text))

    db.query(Task).filter(Task.module_id == lesson.module_id).delete()
    db.query(Test).filter(Test.module_id == lesson.module_id).delete()

    for task in data.get("tasks", []):
        db.add(
            Task(
                module_id=lesson.module_id,
                name=task.get("name", "Задание"),
                description=task.get("description", ""),
            )
        )

    for q in data.get("questions", []):
        db.add(
            Test(
                module_id=lesson.module_id,
                question=q.get("question", ""),
                answers=json.dumps(q.get("answers", [])),
                correct_answer=q.get("correct", ""),
            )
        )

    db.commit()

    try:
        embed_and_add(lesson.id, "lesson", lesson.title)
        embed_and_add(lesson.id, "theory", theory_text)
        for task in data.get("tasks", []):
            embed_and_add(lesson.id, "task", task.get("description", ""))
        for q in data.get("questions", []):
            embed_and_add(lesson.id, "test", q.get("question", ""))
    except Exception as e:
        logger.warning("Embedding error: %s", e)

    return {
        "message": "✅ Контент сгенерирован и сохранён (теория, задачи, тесты)",
        "questions": data.get("questions", []),
        "tasks": data.get("tasks", []),
    }
