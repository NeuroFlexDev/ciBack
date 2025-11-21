# app/routes/course_structure.py
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.course_structure import (
    CourseStructureCreate, CourseStructureUpdate, CourseStructureResponse
)
from app.repositories.course_structure import CourseStructureRepository

router = APIRouter(tags=["Course Structure"])
logger = logging.getLogger(__name__)

@router.post(
    "/course-structure/",
    summary="Создание структуры курса",
    response_model=CourseStructureResponse,
)
def create_course_structure(struct: CourseStructureCreate, db: Session = Depends(get_db)):
    """
    Создает новую структуру курса.
    """
    try:
        new_struct = CourseStructureRepository.create(db, struct)
        logger.info(f"Создана структура курса с ID: {new_struct.id}")
        return CourseStructureResponse(
            id=new_struct.id,
            sections=new_struct.sections,
            tests_per_section=new_struct.tests_per_section,
            lessons_per_section=new_struct.lessons_per_section,
            questions_per_test=new_struct.questions_per_test,
            final_test=new_struct.final_test,
            content_types=new_struct.content_types.split(",") if new_struct.content_types else [],
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка создания структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка создания структуры курса")

@router.get(
    "/course-structure/",
    summary="Получение всех структур курсов",
    response_model=list[CourseStructureResponse],
)
def get_all_course_structures(db: Session = Depends(get_db)):
    """
    Возвращает список всех структур курсов.
    """
    try:
        structs = CourseStructureRepository.list_all(db)
        return [
            CourseStructureResponse(
                id=cs.id,
                sections=cs.sections,
                tests_per_section=cs.tests_per_section,
                lessons_per_section=cs.lessons_per_section,
                questions_per_test=cs.questions_per_test,
                final_test=cs.final_test,
                content_types=cs.content_types.split(",") if cs.content_types else [],
            )
            for cs in structs
        ]
    except Exception as e:
        logger.error(f"Ошибка получения структур курсов: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения структур курсов")

@router.get(
    "/course-structure/{cs_id}",
    summary="Получение структуры курса по ID",
    response_model=CourseStructureResponse,
)
def get_course_structure(cs_id: int, db: Session = Depends(get_db)):
    """
    Возвращает структуру курса по указанному ID.
    """
    try:
        cs = CourseStructureRepository.get_by_id(db, cs_id)
        if not cs:
            raise HTTPException(status_code=404, detail="Структура курса не найдена")
        return CourseStructureResponse(
            id=cs.id,
            sections=cs.sections,
            tests_per_section=cs.tests_per_section,
            lessons_per_section=cs.lessons_per_section,
            questions_per_test=cs.questions_per_test,
            final_test=cs.final_test,
            content_types=cs.content_types.split(",") if cs.content_types else [],
        )
    except Exception as e:
        logger.error(f"Ошибка получения структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения структуры курса")

@router.put(
    "/course-structure/{cs_id}",
    summary="Обновление структуры курса",
    response_model=CourseStructureResponse,
)
def update_course_structure(cs_id: int, struct_update: CourseStructureUpdate, db: Session = Depends(get_db)):
    """
    Обновляет структуру курса по указанному ID.
    """
    try:
        cs = CourseStructureRepository.get_by_id(db, cs_id)
        if not cs:
            raise HTTPException(status_code=404, detail="Структура курса не найдена")
        update_data = struct_update.dict(exclude_unset=True)
        cs = CourseStructureRepository.update(db, cs, update_data)
        logger.info(f"Обновлена структура курса с ID: {cs.id}")
        return CourseStructureResponse(
            id=cs.id,
            sections=cs.sections,
            tests_per_section=cs.tests_per_section,
            lessons_per_section=cs.lessons_per_section,
            questions_per_test=cs.questions_per_test,
            final_test=cs.final_test,
            content_types=cs.content_types.split(",") if cs.content_types else [],
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка обновления структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка обновления структуры курса")

@router.delete("/course-structure/{cs_id}", summary="Удаление структуры курса")
def delete_course_structure(cs_id: int, db: Session = Depends(get_db)):
    """
    Удаляет структуру курса по указанному ID.
    """
    try:
        cs = CourseStructureRepository.get_by_id(db, cs_id)
        if not cs:
            raise HTTPException(status_code=404, detail="Структура курса не найдена")
        CourseStructureRepository.delete(db, cs)
        logger.info(f"Удалена структура курса с ID: {cs_id}")
        return {"message": f"Структура курса с ID {cs_id} успешно удалена"}
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка удаления структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка удаления структуры курса")
