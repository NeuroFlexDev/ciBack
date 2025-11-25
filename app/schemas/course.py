from pydantic import BaseModel, Field
from typing import Optional

# Pydantic-модель для создания курса
class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Название курса")
    description: str = Field(..., min_length=1, description="Описание курса")
    level: int = Field(..., gt=0, description="ID уровня курса (целое число > 0)")
    language: int = Field(..., gt=0, description="ID языка курса (целое число > 0)")

# Pydantic-модель для обновления курса
class CourseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Новое название курса")
    description: Optional[str] = Field(None, min_length=1, description="Новое описание курса")
    level: Optional[int] = Field(None, gt=0, description="Новый ID уровня курса (целое число > 0)")
    language: Optional[int] = Field(None, gt=0, description="Новый ID языка курса (целое число > 0)")

# Pydantic-модель для ответа
class CourseResponse(BaseModel):
    id: int
    title: str
    description: str
    level: int
    language: int
    is_deleted: bool
    class Config:
        orm_mode = True
