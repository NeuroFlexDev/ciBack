from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatCreate(BaseModel):
    name: str = Field(default="New chat", min_length=1)
    course_id: int | None = None


class ChatOut(BaseModel):
    id: int
    name: str
    model: str | None = None
    engine: str | None = None
    course_id: int | None = None
    is_deleted: bool = False

    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    id: int
    author: str
    role: str
    text: str
    created_at: datetime | None = None
    is_deleted: bool = False


class MessageIn(BaseModel):
    chat_id: int | None = None
    text: str = Field(..., min_length=1)
    engine: str | None = "lc_giga"
    model: str | None = None
    course_id: int | None = None


class ModelPatch(BaseModel):
    model: str = Field(..., min_length=1)
    engine: str | None = None
