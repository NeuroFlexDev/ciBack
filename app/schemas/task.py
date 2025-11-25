from pydantic import BaseModel

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
    class Config:
        orm_mode = True
