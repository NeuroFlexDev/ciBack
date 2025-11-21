# app/routes/modules.py
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.module import ModuleCreate, ModuleUpdate, ModuleResponse
from app.repositories.module import ModuleRepository

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "/courses/{course_id}/modules/",
    response_model=ModuleResponse,
    summary="Создание модуля для курса",
)
def add_module(course_id: int, module: ModuleCreate, db: Session = Depends(get_db)):
    """
    Создает новый модуль для курса с заданным course_id.
    """
    try:
        new_module = ModuleRepository.add_module(db, course_id, module)
        logger.info("Создан модуль: ID=%s для курса ID=%s", new_module.id, course_id)
        return ModuleResponse(id=new_module.id, title=new_module.title, course_id=new_module.course_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get(
    "/courses/{course_id}/modules/",
    response_model=list[ModuleResponse],
    summary="Получение модулей курса",
)
def get_modules(course_id: int, db: Session = Depends(get_db)):
    """
    Возвращает список всех модулей для курса с указанным course_id.
    """
    try:
        modules = ModuleRepository.list_modules(db, course_id)
        return [ModuleResponse(id=mod.id, title=mod.title, course_id=mod.course_id) for mod in modules]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get(
    "/modules/{module_id}",
    response_model=ModuleResponse,
    summary="Получение модуля по ID",
)
def get_module(module_id: int, db: Session = Depends(get_db)):
    """
    Возвращает данные модуля по его ID.
    """
    mod = ModuleRepository.get_by_id(db, module_id)
    if not mod:
        raise HTTPException(status_code=404, detail="Module not found")
    return ModuleResponse(id=mod.id, title=mod.title, course_id=mod.course_id)

@router.put("/modules/{module_id}", response_model=ModuleResponse, summary="Обновление модуля")
def update_module(module_id: int, module_update: ModuleUpdate, db: Session = Depends(get_db)):
    """
    Обновляет данные модуля по его ID.
    """
    mod = ModuleRepository.get_by_id(db, module_id)
    if not mod:
        raise HTTPException(status_code=404, detail="Module not found")
    update_data = module_update.dict(exclude_unset=True)
    mod = ModuleRepository.update_module(db, mod, update_data)
    logger.info("Обновлен модуль: ID=%s", module_id)
    return ModuleResponse(id=mod.id, title=mod.title, course_id=mod.course_id)

@router.delete("/modules/{module_id}", summary="Удаление модуля")
def delete_module(module_id: int, db: Session = Depends(get_db)):
    """
    Удаляет модуль по его ID.
    """
    mod = ModuleRepository.get_by_id(db, module_id)
    if not mod:
        raise HTTPException(status_code=404, detail="Module not found")
    ModuleRepository.delete_module(db, mod)
    logger.info("Удален модуль: ID=%s", module_id)
    return {"message": f"Module with ID {module_id} successfully deleted."}
