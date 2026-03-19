import json
import os
import tempfile

import fitz
from docx import Document
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from app.repositories.upload import UploadRepository
from app.services.embedding_service import index_course_document
from app.services.generation_service import generate_from_prompt


class UploadService:
    @staticmethod
    def generate_and_save_lesson_content(db: Session, course_id: int, lesson_id: int):
        course = UploadRepository.get_course(db, course_id)
        if not course:
            raise HTTPException(404, "❌ Курс не найден")
        lesson = UploadRepository.get_lesson(db, lesson_id)
        if not lesson:
            raise HTTPException(404, "❌ Урок не найден")
        content_data = generate_from_prompt(
            "lesson_content_prompt.j2",
            course_name=course.name,
            course_description=course.description,
            lesson_title=lesson.title,
        )
        existing = UploadRepository.get_theory(db, lesson.id)
        if existing:
            existing.content = content_data.get("theory", "")
        else:
            theory = Theory(lesson_id=lesson.id, content=content_data.get("theory", ""))
            db.add(theory)
        db.query(Task).filter(Task.module_id == lesson.module_id).delete()
        db.query(Test).filter(Test.module_id == lesson.module_id).delete()
        for task in content_data.get("tasks", []):
            db.add(
                Task(
                    module_id=lesson.module_id,
                    name=task.get("name", "Задание"),
                    description=task.get("description", ""),
                )
            )
        for question in content_data.get("questions", []):
            db.add(
                Test(
                    module_id=lesson.module_id,
                    question=question.get("question", ""),
                    answers=json.dumps(question.get("answers", [])),
                    correct_answer=question.get("correct", ""),
                )
            )
        db.commit()
        return {
            "message": "Контент сгенерирован и сохранен",
            "questions": content_data.get("questions", []),
            "tasks": content_data.get("tasks", []),
        }

    @staticmethod
    def extract_text(file_path: str, content_type: str) -> str:
        if content_type == "application/pdf":
            text = ""
            with fitz.open(file_path) as doc:
                for page in doc:
                    text += page.get_text()
            return text
        if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            doc = Document(file_path)
            return "\n".join(paragraph.text for paragraph in doc.paragraphs)
        if content_type == "text/plain":
            with open(file_path, encoding="utf-8") as file_handle:
                return file_handle.read()
        raise HTTPException(status_code=400, detail="Unsupported file type")

    @staticmethod
    def upload_and_update_description(db: Session, course_id: int, file: UploadFile, temp_dir=None):
        course = UploadRepository.get_course(db, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        suffix = os.path.splitext(file.filename)[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir) as tmp:
            tmp.write(file.file.read())
            temp_path = tmp.name

        try:
            extracted = UploadService.extract_text(temp_path, file.content_type)
            result = generate_from_prompt(
                "rag_summary_prompt.j2",
                course_title=course.name,
                original_text=extracted,
            )
            summary = result.get("summary")
            if not summary:
                raise HTTPException(status_code=500, detail="Could not generate summary")

            indexed_chunks = index_course_document(
                course_id,
                extracted,
                source_name=file.filename,
                content_type=file.content_type,
            )

            course.description = summary
            db.commit()

            return {
                "message": "Course description updated and document indexed",
                "summary": summary,
                "indexed_chunks": indexed_chunks,
            }
        finally:
            os.remove(temp_path)
