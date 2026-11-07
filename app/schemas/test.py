from typing import List

from pydantic import BaseModel, ConfigDict


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
    model_config = ConfigDict(from_attributes=True)
