from pydantic import BaseModel

class FeedbackInput(BaseModel):
    lesson_id: int
    type: str = "general"
    comment: str = ""
    rating: int = 5
