from pydantic import BaseModel

class LessonRequest(BaseModel):
    lesson_id: int
