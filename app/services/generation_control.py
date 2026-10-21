from sqlalchemy import select
from datetime import datetime, timedelta
from app.core.config import settings
from app.models.generation_run import GenerationRun
from app.services.generation_authorization import generation_access_active


class GenerationCancelled(Exception):
    def __init__(self, message, code="user_cancelled"):
        super().__init__(message)
        self.code = code


def check_cancelled(db, run_id):
    state = db.execute(select(GenerationRun.cancel_requested, GenerationRun.started_at).where(GenerationRun.id == run_id)).first()
    if state and state.cancel_requested:
        raise GenerationCancelled("Генерация остановлена пользователем; завершённые этапы сохранены")
    if not generation_access_active(db, run_id):
        raise GenerationCancelled("Доступ автора отозван; завершённые этапы сохранены", code="access_revoked")
    if state and state.started_at and state.started_at + timedelta(seconds=settings.AI_JOB_TIMEOUT_SECONDS) <= datetime.utcnow():
        raise GenerationCancelled("Достигнут лимит времени генерации; завершённые этапы сохранены", code="time_limit")
