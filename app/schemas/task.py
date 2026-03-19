from pydantic import BaseModel, ConfigDict

class TaskCreate(BaseModel):
    name: str
    description: str = ""

class TaskUpdate(BaseModel):
    name: str
    description: str = ""

class TaskResponse(BaseModel):
    id: int
    name: str
    description: str
    module_id: int
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)
