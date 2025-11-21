from pydantic import BaseModel, Field, validator
from typing import List, Optional

# Pydantic-модель для создания структуры курса
class CourseStructureCreate(BaseModel):
    sections: int = Field(..., gt=0, description="Количество секций курса")
    tests_per_section: int = Field(..., ge=0, description="Количество тестов на секцию")
    lessons_per_section: int = Field(..., gt=0, description="Количество уроков на секцию")
    questions_per_test: int = Field(..., ge=0, description="Количество вопросов в тесте")
    final_test: bool = Field(..., description="Наличие финального теста")
    content_types: List[str] = Field(default_factory=list, description="Список типов контента")

    @validator("content_types", each_item=True)
    def non_empty_content_types(cls, v):
        if not v.strip():
            raise ValueError("Элементы content_types не должны быть пустыми")
        return v.strip()

# Pydantic-модель для обновления структуры курса (все поля опциональны)
class CourseStructureUpdate(BaseModel):
    sections: Optional[int] = Field(None, gt=0, description="Количество секций курса")
    tests_per_section: Optional[int] = Field(None, ge=0, description="Количество тестов на секцию")
    lessons_per_section: Optional[int] = Field(None, gt=0, description="Количество уроков на секцию")
    questions_per_test: Optional[int] = Field(None, ge=0, description="Количество вопросов в тесте")
    final_test: Optional[bool] = Field(None, description="Наличие финального теста")
    content_types: Optional[List[str]] = Field(None, description="Список типов контента")

    @validator("content_types", each_item=True)
    def non_empty_content_types(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Элементы content_types не должны быть пустыми")
        return v.strip() if v else v

# Pydantic-модель для ответа
class CourseStructureResponse(BaseModel):
    id: int
    sections: int
    tests_per_section: int
    lessons_per_section: int
    questions_per_test: int
    final_test: bool
    content_types: List[str]
    is_deleted: bool
    class Config:
        orm_mode = True
