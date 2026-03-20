import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.user import User
from app.repositories.generation import GenerationRepository
from app.services.access_control import ensure_can_manage_course
from app.services.generation_service import DEFAULT_ENGINE, DEFAULT_MODEL, course_kwargs, llm_json
from app.services.auth import get_current_user_dep

router = APIRouter(prefix="/courses")
logger = logging.getLogger(__name__)


@router.get("/{course_id}/generate_modules", summary="Генерация и сохранение модулей курса")
def generate_and_save_modules(
    course_id: int,
    cs_id: int = Query(..., description="ID структуры курса"),
    engine: str = Query(DEFAULT_ENGINE),
    model: str | None = Query(DEFAULT_MODEL),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    course = GenerationRepository.get_course(db, course_id)
    if not course:
        raise HTTPException(404, "Курс не найден")
    ensure_can_manage_course(user, course)

    cs = GenerationRepository.get_structure(db, cs_id)
    if not cs:
        raise HTTPException(404, "Структура курса не найдена")

    data = llm_json("module_prompt.j2", engine=engine, model=model, **course_kwargs(course, cs))
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise HTTPException(500, "LLM не вернул корректный ключ 'modules'")
    db.query(Module).filter(Module.course_id == course.id).delete()
    try:
        for generated_module in modules:
            created_module = GenerationRepository.add_module(
                db,
                course.id,
                generated_module.get("title") or "Без названия",
            )
            for lesson in generated_module.get("lessons", []):
                db.add(
                    Lesson(
                        module_id=created_module.id,
                        title=lesson.get("title", "Без названия"),
                        description=lesson.get("description", ""),
                    )
                )
        db.commit()
        return {"message": "Модули успешно сгенерированы и сохранены"}
    except Exception as exc:
        db.rollback()
        logger.error("DB error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(500, f"Ошибка сохранения модулей: {exc}") from exc
