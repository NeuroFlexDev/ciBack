from pydantic import BaseModel, Field
from typing import Literal


class ChatCreate(BaseModel):
    name: str = "Новый чат"
    course_id: int | None = None


class ChatRename(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ChatOut(BaseModel):
    id: int
    course_id: int | None = None
    name: str
    model: str | None = None
    engine: str | None = None
    is_deleted: bool


class MessageOut(BaseModel):
    id: int
    author: str
    text: str
    is_deleted: bool
    request_id: str | None = None
    status: str | None = None
    error_code: str | None = None


class MessageIn(BaseModel):
    chat_id: int | None = None
    text: str = Field(min_length=1, max_length=12000)
    engine: Literal["vsellm", "lc_giga", "lc_hf", "raw_giga", "raw_hf"] | None = None
    model: str | None = None
    request_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ModelPatch(BaseModel):
    model: str
    engine: str | None = None
