from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.agent import ImproveTheoryRequest, ImproveTheoryResponse
from app.services.agent_service import improve_theory_for_lesson
from app.services.auth import get_current_user_dep

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/improve-theory", summary="Improve lesson theory", response_model=ImproveTheoryResponse)
def improve_theory(
    payload: ImproveTheoryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    return improve_theory_for_lesson(
        db,
        user=user,
        lesson_id=payload.lesson_id,
        goal=payload.goal,
    )
