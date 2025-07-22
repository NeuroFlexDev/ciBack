import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.course import Course
from app.models.module import Module

router = APIRouter()
logger = logging.getLogger(__name__)


# Pydantic-модель для создания модуля
class ModuleCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Название модуля")


# Pydantic-модель для обновления модуля (опциональные поля)
class ModuleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, description="Новое название модуля")


# Pydantic-модель для ответа
class ModuleResponse(BaseModel):
    id: int
    title: str
    course_id: int

    class Config:
        orm_mode = True


@router.post(
    "/courses/{course_id}/modules/",
    response_model=ModuleResponse,
    summary="Создание модуля для курса",
)
def add_module(course_id: int, module: ModuleCreate, db: Session = Depends(get_db)):
    """
    Создает новый модуль для курса с заданным course_id.
    """
    # Проверяем, существует ли курс с данным course_id
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    new_module = Module(title=module.title, course_id=course_id)
    db.add(new_module)
    db.commit()
    db.refresh(new_module)
    logger.info("Создан модуль: ID=%s для курса ID=%s", new_module.id, course_id)
    return ModuleResponse(id=new_module.id, title=new_module.title, course_id=new_module.course_id)


@router.get(
    "/courses/{course_id}/modules/",
    response_model=list[ModuleResponse],
    summary="Получение модулей курса",
)
def get_modules(course_id: int, db: Session = Depends(get_db)):
    """
    Возвращает список всех модулей для курса с указанным course_id.
    """
    # Проверяем, существует ли курс с данным course_id
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    modules = db.query(Module).filter(Module.course_id == course_id).all()
    return [ModuleResponse(id=mod.id, title=mod.title, course_id=mod.course_id) for mod in modules]


@router.get(
    "/modules/{module_id}",
    response_model=ModuleResponse,
    summary="Получение модуля по ID",
)
def get_module(module_id: int, db: Session = Depends(get_db)):
    """
    Возвращает данные модуля по его ID.
    """
    mod = db.query(Module).filter(Module.id == module_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Module not found")
    return ModuleResponse(id=mod.id, title=mod.title, course_id=mod.course_id)


@router.put("/modules/{module_id}", response_model=ModuleResponse, summary="Обновление модуля")
def update_module(module_id: int, module_update: ModuleUpdate, db: Session = Depends(get_db)):
    """
    Обновляет данные модуля по его ID.
    """
    mod = db.query(Module).filter(Module.id == module_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Module not found")

    update_data = module_update.dict(exclude_unset=True)
    if "title" in update_data:
        mod.title = update_data["title"]

    db.commit()
    db.refresh(mod)
    logger.info("Обновлен модуль: ID=%s", module_id)
    return ModuleResponse(id=mod.id, title=mod.title, course_id=mod.course_id)


@router.delete("/modules/{module_id}", summary="Удаление модуля")
def delete_module(module_id: int, db: Session = Depends(get_db)):
    """
    Удаляет модуль по его ID.
    """
    mod = db.query(Module).filter(Module.id == module_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Module not found")

    db.delete(mod)
    db.commit()
    logger.info("Удален модуль: ID=%s", module_id)
    return {"message": f"Module with ID {module_id} successfully deleted."}
