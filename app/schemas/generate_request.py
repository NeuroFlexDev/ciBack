# app/models/generate_request.py
from pydantic import BaseModel


class GenerateRequest(BaseModel):
    title: str  # Название курса
    difficulty: str  # Уровень (например: "Начальный", "Средний")
    language: str = "Russian"  # Язык (по желанию)
    # Можешь добавить любые поля, нужные для генерации
