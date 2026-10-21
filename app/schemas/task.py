from pydantic import BaseModel, ConfigDict, Field

class TaskCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=1000)
    description: str = Field(default='', max_length=200000)

class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=1000)
    description: str = Field(default='', max_length=200000)
    expected_revision: int = Field(gt=0)

class TaskResponse(BaseModel):
    id: int
    name: str
    description: str
    module_id: int
    is_deleted: bool
    revision: int
    model_config = ConfigDict(from_attributes=True)
