from pydantic import BaseModel

class TheoryCreate(BaseModel):
    lesson_id: int
    content: str

class TheoryUpdate(BaseModel):
    content: str

class TheoryResponse(BaseModel):
    lesson_id: int
    content: str
    is_deleted: bool
    class Config:
        orm_mode = True
