from pydantic import BaseModel, Field
from typing import Optional

# Pydantic-модель для создания модуля
class ModuleCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Название модуля")

# Pydantic-модель для обновления модуля (опциональные поля)
class ModuleUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, description="Новое название модуля")

# Pydantic-модель для ответа
class ModuleResponse(BaseModel):
    id: int
    title: str
    course_id: int
    is_deleted: bool
    class Config:
        orm_mode = True
