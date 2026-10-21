from sqlalchemy import select
from app.models.generation_run import GenerationRun


class GenerationCancelled(Exception):
    pass


def check_cancelled(db, run_id):
    requested = db.execute(select(GenerationRun.cancel_requested).where(GenerationRun.id == run_id)).scalar()
    if requested:
        raise GenerationCancelled("Генерация остановлена пользователем; завершённые этапы сохранены")
