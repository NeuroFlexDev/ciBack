from typing import List

from pydantic import BaseModel, ConfigDict, model_validator


class TestCreate(BaseModel):
    question: str
    answers: List[str]
    correct: str

    @model_validator(mode="after")
    def validate_correct_answer(self) -> "TestCreate":
        if not self.answers:
            raise ValueError("answers must not be empty")
        if self.correct not in self.answers:
            raise ValueError("correct must be one of answers")
        return self


class TestUpdate(BaseModel):
    question: str | None = None
    answers: List[str] | None = None
    correct: str | None = None

    @model_validator(mode="after")
    def validate_correct_answer(self) -> "TestUpdate":
        if self.answers is not None and not self.answers:
            raise ValueError("answers must not be empty")
        if self.answers is not None and self.correct is not None and self.correct not in self.answers:
            raise ValueError("correct must be one of answers")
        return self


class TestResponse(BaseModel):
    id: int
    question: str
    answers: List[str]
    correct: str
    module_id: int
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)
