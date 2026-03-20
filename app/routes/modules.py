import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.repositories.course import CourseRepository
from app.repositories.module import ModuleRepository
from app.schemas.module import ModuleCreate, ModuleResponse, ModuleUpdate
from app.services.access_control import ensure_can_manage_course, ensure_can_manage_module
from app.services.auth import get_current_user_dep

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/courses/{course_id}/modules/",
    response_model=ModuleResponse,
    summary="Создание модуля для курса",
)
def add_module(
    course_id: int,
    module: ModuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    try:
        course = CourseRepository.get_by_id(db, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        ensure_can_manage_course(user, course)
        new_module = ModuleRepository.add_module(db, course_id, module)
        logger.info("Создан модуль: ID=%s для курса ID=%s", new_module.id, course_id)
        return ModuleResponse(
            id=new_module.id,
            title=new_module.title,
            course_id=new_module.course_id,
            is_deleted=new_module.is_deleted,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/courses/{course_id}/modules/",
    response_model=list[ModuleResponse],
    summary="Получение модулей курса",
)
def get_modules(course_id: int, db: Session = Depends(get_db)):
    try:
        modules = ModuleRepository.list_modules(db, course_id)
        return [
            ModuleResponse(
                id=module.id,
                title=module.title,
                course_id=module.course_id,
                is_deleted=module.is_deleted,
            )
            for module in modules
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/modules/{module_id}",
    response_model=ModuleResponse,
    summary="Получение модуля по ID",
)
def get_module(module_id: int, db: Session = Depends(get_db)):
    module = ModuleRepository.get_by_id(db, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return ModuleResponse(
        id=module.id,
        title=module.title,
        course_id=module.course_id,
        is_deleted=module.is_deleted,
    )


@router.put("/modules/{module_id}", response_model=ModuleResponse, summary="Обновление модуля")
def update_module(
    module_id: int,
    module_update: ModuleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    module = ModuleRepository.get_by_id(db, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    ensure_can_manage_module(user, module)
    update_data = module_update.model_dump(exclude_unset=True)
    module = ModuleRepository.update_module(db, module, update_data)
    logger.info("Обновлен модуль: ID=%s", module_id)
    return ModuleResponse(
        id=module.id,
        title=module.title,
        course_id=module.course_id,
        is_deleted=module.is_deleted,
    )


@router.delete("/modules/{module_id}", summary="Удаление модуля")
def delete_module(
    module_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    module = ModuleRepository.get_by_id(db, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    ensure_can_manage_module(user, module)
    ModuleRepository.delete_module(db, module)
    logger.info("Удален модуль: ID=%s", module_id)
    return {"message": f"Module with ID {module_id} successfully deleted."}
