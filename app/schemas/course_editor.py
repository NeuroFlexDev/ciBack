from pydantic import BaseModel, Field, model_validator


class ModuleCreateEditor(BaseModel):
    title: str = Field(min_length=1)


class ModuleUpdateEditor(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    expected_revision: int = Field(gt=0)


class LessonCreateEditor(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    content: str = ""


class LessonUpdateEditor(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    content: str | None = None
    expected_revision: int = Field(gt=0)


class StructuredTestCreate(BaseModel):
    question: str = Field(min_length=1)
    answers: list[str] = Field(default_factory=list)
    correct_answer: str = ""


class LegacyTestCreate(BaseModel):
    test: str = Field(min_length=1)
    description: str


class TestCreateEditor(BaseModel):
    question: str | None = None
    answers: list[str] | None = None
    correct_answer: str | None = None
    test: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.question:
            self.answers = self.answers or []
            self.correct_answer = self.correct_answer or ""
            return self
        if self.test and self.description is not None:
            return self
        raise ValueError("provide question/answers/correct_answer or legacy test/description")


class TestUpdateEditor(BaseModel):
    question: str = Field(min_length=1)
    answers: list[str]
    correct_answer: str
    expected_revision: int = Field(gt=0)


class OrderItem(BaseModel):
    id: int = Field(gt=0)
    position: int = Field(ge=0)
    expected_revision: int = Field(gt=0)


class OrderUpdate(BaseModel):
    items: list[OrderItem]

    @model_validator(mode="after")
    def validate_unique(self):
        ids = [item.id for item in self.items]
        positions = [item.position for item in self.items]
        if len(ids) != len(set(ids)) or len(positions) != len(set(positions)):
            raise ValueError("ids and positions must be unique")
        if sorted(positions) != list(range(len(positions))):
            raise ValueError("positions must be contiguous from zero")
        return self
