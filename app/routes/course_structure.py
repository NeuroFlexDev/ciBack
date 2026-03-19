import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.repositories.course_structure import CourseStructureRepository
from app.schemas.common import MessageResponse
from app.schemas.course_structure import (
    CourseStructureCreate,
    CourseStructureResponse,
    CourseStructureUpdate,
)

router = APIRouter(tags=["Course Structure"])
logger = logging.getLogger(__name__)


def parse_content_types(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def build_course_structure_response(structure) -> CourseStructureResponse:
    return CourseStructureResponse(
        id=structure.id,
        sections=structure.sections,
        tests_per_section=structure.tests_per_section,
        lessons_per_section=structure.lessons_per_section,
        questions_per_test=structure.questions_per_test,
        final_test=structure.final_test,
        content_types=parse_content_types(structure.content_types),
        is_deleted=structure.is_deleted,
    )


@router.post(
    "/course-structure/",
    summary="Создание структуры курса",
    response_model=CourseStructureResponse,
)
def create_course_structure(struct: CourseStructureCreate, db: Session = Depends(get_db)):
    try:
        return build_course_structure_response(CourseStructureRepository.create(db, struct))
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Failed to create course structure: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create course structure")


@router.get(
    "/course-structure/",
    summary="Получение всех структур курсов",
    response_model=list[CourseStructureResponse],
)
def get_all_course_structures(db: Session = Depends(get_db)):
    try:
        return [build_course_structure_response(item) for item in CourseStructureRepository.list_all(db)]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to list course structures: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list course structures")


@router.get(
    "/course-structure/{cs_id}",
    summary="Получение структуры курса по ID",
    response_model=CourseStructureResponse,
)
def get_course_structure(cs_id: int, db: Session = Depends(get_db)):
    try:
        cs = CourseStructureRepository.get_by_id(db, cs_id)
        if not cs:
            raise HTTPException(status_code=404, detail="Course structure not found")
        return build_course_structure_response(cs)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get course structure: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get course structure")


@router.put(
    "/course-structure/{cs_id}",
    summary="Обновление структуры курса",
    response_model=CourseStructureResponse,
)
def update_course_structure(cs_id: int, struct_update: CourseStructureUpdate, db: Session = Depends(get_db)):
    try:
        cs = CourseStructureRepository.get_by_id(db, cs_id)
        if not cs:
            raise HTTPException(status_code=404, detail="Course structure not found")
        update_data = struct_update.model_dump(exclude_unset=True)
        updated = CourseStructureRepository.update(db, cs, update_data)
        return build_course_structure_response(updated)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Failed to update course structure: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update course structure")


@router.delete(
    "/course-structure/{cs_id}",
    summary="Удаление структуры курса",
    response_model=MessageResponse,
)
def delete_course_structure(cs_id: int, db: Session = Depends(get_db)):
    try:
        cs = CourseStructureRepository.get_by_id(db, cs_id)
        if not cs:
            raise HTTPException(status_code=404, detail="Course structure not found")
        CourseStructureRepository.delete(db, cs)
        return MessageResponse(message="Course structure deleted")
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Failed to delete course structure: %s", str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete course structure")
