from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

# Pydantic-модель для создания урока
class LessonCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Название урока")
    description: str = Field(..., min_length=1, description="Описание урока")

# Pydantic-модель для обновления урока (поля опциональные)
class LessonUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Новое название урока")
    description: Optional[str] = Field(None, min_length=1, description="Новое описание урока")

# Pydantic-модель для ответа
class LessonResponse(BaseModel):
    id: int
    title: str
    description: str
    module_id: int
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)
