import json
import os
import tempfile

import fitz  # PyMuPDF
from docx import Document
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from app.services.generation_service import generate_from_prompt

router = APIRouter()


class LessonRequest(BaseModel):
    lesson_id: int


@router.post("/courses/{course_id}/generate_lesson_content", summary="Генерация контента урока")
def generate_and_save_lesson_content(
    course_id: int, payload: LessonRequest, db: Session = Depends(get_db)
):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    lesson = db.query(Lesson).filter(Lesson.id == payload.lesson_id).first()
    if not lesson:
        raise HTTPException(404, "❌ Урок не найден")

    content_data = generate_from_prompt(
        "lesson_content_prompt.j2",
        course_name=course.name,
        course_description=course.description,
        lesson_title=lesson.title,
    )

    # Сохраняем теорию
    existing = db.query(Theory).filter(Theory.lesson_id == lesson.id).first()
    if existing:
        existing.content = content_data.get("theory", "")
    else:
        theory = Theory(lesson_id=lesson.id, content=content_data.get("theory", ""))
        db.add(theory)

    # Удалим старые задачи и тесты, если нужно (можно адаптировать под soft-update)
    db.query(Task).filter(Task.module_id == lesson.module_id).delete()
    db.query(Test).filter(Test.module_id == lesson.module_id).delete()

    # Сохраняем задачи
    for task in content_data.get("tasks", []):
        db.add(
            Task(
                module_id=lesson.module_id,
                name=task.get("name", "Задание"),
                description=task.get("description", ""),
            )
        )

    # Сохраняем тесты
    for q in content_data.get("questions", []):
        db.add(
            Test(
                module_id=lesson.module_id,
                question=q.get("question", ""),
                answers=json.dumps(q.get("answers", [])),
                correct_answer=q.get("correct", ""),
            )
        )

    db.commit()

    return {
        "message": "✅ Контент сгенерирован и сохранён (теория, задачи, тесты)",
        "questions": content_data.get("questions", []),
        "tasks": content_data.get("tasks", []),
    }


@router.post(
    "/courses/{course_id}/upload-description",
    summary="RAG: загрузка файла и обновление описания курса",
)
def upload_and_update_description(
    course_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    # Проверка курса
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(404, detail="❌ Курс не найден")

    # Сохраняем файл во временную папку
    suffix = os.path.splitext(file.filename)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.file.read())
        temp_path = tmp.name

    try:
        # Извлечение текста
        extracted = extract_text(temp_path, file.content_type)

        # Суммаризация через LLM
        result = generate_from_prompt(
            "rag_summary_prompt.j2", course_title=course.name, original_text=extracted
        )

        summary = result.get("summary")
        if not summary:
            raise HTTPException(500, "❌ Не удалось получить summary от LLM")

        # Обновляем описание
        course.description = summary
        db.commit()

        return {
            "message": "✅ Описание курса обновлено на основе загруженного документа",
            "summary": summary,
        }

    finally:
        os.remove(temp_path)


def extract_text(file_path: str, content_type: str) -> str:
    if content_type == "application/pdf":
        text = ""
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
        return text

    elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)

    elif content_type == "text/plain":
        with open(file_path, encoding="utf-8") as f:
            return f.read()

    raise HTTPException(400, detail="❌ Неподдерживаемый тип файла")
