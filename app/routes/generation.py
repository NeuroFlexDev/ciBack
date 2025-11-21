from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.schemas.generation import LessonRequest
from app.repositories.generation import GenerationRepository
from app.services.generation_service import course_kwargs, llm_json, DEFAULT_ENGINE, DEFAULT_MODEL
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from app.services.embedding_service import embed_and_add
from app.database.db import get_db
import json, logging, traceback

router = APIRouter(prefix="/courses", tags=["Generation"])
logger = logging.getLogger(__name__)

@router.get("/{course_id}/generate_modules", summary="Генерация и сохранение модулей курса")
def generate_and_save_modules(
    course_id: int,
    cs_id: int = Query(..., description="ID структуры курса"),
    engine: str = Query(DEFAULT_ENGINE),
    model: str | None = Query(DEFAULT_MODEL),
    db: Session = Depends(get_db),
):
    course = GenerationRepository.get_course(db, course_id)
    if not course:
        raise HTTPException(404, "❌ Курс не найден")

    cs = GenerationRepository.get_structure(db, cs_id)
    if not cs:
        raise HTTPException(404, "❌ Структура курса не найдена")

    data = llm_json("module_prompt.j2", engine=engine, model=model, **course_kwargs(course, cs))
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise HTTPException(500, "LLM не вернул корректный ключ 'modules'")
    db.query(Module).filter(Module.course_id == course.id).delete()
    try:
        for mod in modules:
            m = GenerationRepository.add_module(db, course.id, mod.get("title") or "Без названия")
            for lesson in mod.get("lessons", []):
                db.add(Lesson(module_id=m.id,
                              title=lesson.get("title", "Без названия"),
                              description=lesson.get("description", "")))
            # ... и далее по аналогии для tests/tasks
        db.commit()
        return {"message": "✅ Модули успешно сгенерированы и сохранены"}
    except Exception as e:
        db.rollback()
        logger.error("DB error: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"❌ Ошибка сохранения модулей: {e}")
