from pydantic import BaseModel
from typing import List

class TestCreate(BaseModel):
    test: str
    description: str

class TestUpdate(BaseModel):
    question: str
    answers: List[str]
    correct: str

class TestResponse(BaseModel):
    id: int
    question: str
    answers: List[str]
    correct: str
    module_id: int
    is_deleted: bool
    class Config:
        orm_mode = True
