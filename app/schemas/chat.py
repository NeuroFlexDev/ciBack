from pydantic import BaseModel, ConfigDict

# ---------- Schemas ----------
class ChatCreate(BaseModel):
    name: str = "Новый чат"

class ChatOut(BaseModel):
    id: int
    name: str
    model: str | None = None
    engine: str | None = None
    is_deleted: bool
    model_config = ConfigDict(from_attributes=True)

class MessageOut(BaseModel):
    id: int
    author: str  # 'user' | 'bot'
    text: str
    is_deleted: bool

class MessageIn(BaseModel):
    chat_id: int | None = None
    text: str
    engine: str | None = "lc_giga"
    model: str | None = None


class ModelPatch(BaseModel):
    model: str
    engine: str | None = None
