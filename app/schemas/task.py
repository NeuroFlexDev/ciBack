from pydantic import BaseModel, ConfigDict


class TaskCreate(BaseModel):
    name: str
    description: str | None = ""


class TaskUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class TaskResponse(BaseModel):
    id: int
    name: str
    description: str | None
    module_id: int
    lesson_id: int | None = None
    is_deleted: bool

    model_config = ConfigDict(from_attributes=True)
