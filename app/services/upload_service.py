"""Deprecated import surface; parsing uses the bounded production pipeline."""
from fastapi import HTTPException
from app.core.config import settings
from app.services.document_safety import bounded_extract_blocks

class UploadService:
    @staticmethod
    def extract_text(file_path: str, content_type: str) -> str:
        with open(file_path, "rb") as source:
            content = source.read(settings.max_document_bytes + 1)
        return "\n\n".join(block.text for block in bounded_extract_blocks(content, content_type))

    @staticmethod
    def upload_and_update_description(*args, **kwargs):
        raise HTTPException(410, "Используйте мастер курса с версионированными источниками")

    @staticmethod
    def generate_and_save_lesson_content(*args, **kwargs):
        raise HTTPException(410, "Используйте generation-runs с проверкой качества")
