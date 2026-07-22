from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.course import Course
from app.models.user import User
from app.services.generation_service import generate_from_prompt
from app.services.auth_service import get_current_user
import os
import tempfile
import fitz  # PyMuPDF
from docx import Document

router = APIRouter()

@router.post("/courses/{course_id}/upload-description", summary="RAG: загрузка файла и обновление описания курса")
def upload_and_update_description(
    course_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Проверка курса
    course = db.query(Course).filter(Course.id == course_id, Course.owner_id == current_user.id).first()
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
            "rag_summary_prompt.j2",
            course_title=course.name,
            original_text=extracted
        )

        summary = result.get("summary")
        if not summary:
            raise HTTPException(500, "❌ Не удалось получить summary от LLM")

        # Обновляем описание
        course.description = summary
        db.commit()

        return {
            "message": "✅ Описание курса обновлено на основе загруженного документа",
            "summary": summary
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
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    raise HTTPException(400, detail="❌ Неподдерживаемый тип файла")
