from pydantic import BaseModel, ConfigDict

class TheoryCreate(BaseModel):
    lesson_id: int
    content: str

class TheoryUpdate(BaseModel):
    content: str

class TheoryResponse(BaseModel):
    lesson_id: int
    content: str
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)
