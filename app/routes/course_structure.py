import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from typing import List

from app.database.db import get_db
from app.models.course_structure import CourseStructure

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic-модель для создания структуры курса
class CourseStructureCreate(BaseModel):
    sections: int = Field(..., gt=0, description="Количество секций курса")
    tests_per_section: int = Field(..., ge=0, description="Количество тестов на секцию")
    lessons_per_section: int = Field(..., gt=0, description="Количество уроков на секцию")
    questions_per_test: int = Field(..., ge=0, description="Количество вопросов в тесте")
    final_test: bool = Field(..., description="Наличие финального теста")
    content_types: List[str] = Field(default_factory=list, description="Список типов контента")

    @validator("content_types", each_item=True)
    def non_empty_content_types(cls, v):
        if not v.strip():
            raise ValueError("Элементы content_types не должны быть пустыми")
        return v.strip()

# Pydantic-модель для обновления структуры курса (все поля опциональны)
class CourseStructureUpdate(BaseModel):
    sections: int | None = Field(None, gt=0, description="Количество секций курса")
    tests_per_section: int | None = Field(None, ge=0, description="Количество тестов на секцию")
    lessons_per_section: int | None = Field(None, gt=0, description="Количество уроков на секцию")
    questions_per_test: int | None = Field(None, ge=0, description="Количество вопросов в тесте")
    final_test: bool | None = Field(None, description="Наличие финального теста")
    content_types: List[str] | None = Field(None, description="Список типов контента")

    @validator("content_types", each_item=True)
    def non_empty_content_types(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Элементы content_types не должны быть пустыми")
        return v.strip() if v else v

# Pydantic-модель для ответа
class CourseStructureResponse(BaseModel):
    id: int
    sections: int
    tests_per_section: int
    lessons_per_section: int
    questions_per_test: int
    final_test: bool
    content_types: List[str]

    class Config:
        orm_mode = True

@router.post("/course-structure/", summary="Создание структуры курса", response_model=CourseStructureResponse)
def create_course_structure(struct: CourseStructureCreate, db: Session = Depends(get_db)):
    """
    Создает новую структуру курса.
    """
    try:
        new_struct = CourseStructure(
            sections=struct.sections,
            tests_per_section=struct.tests_per_section,
            lessons_per_section=struct.lessons_per_section,
            questions_per_test=struct.questions_per_test,
            final_test=struct.final_test,
            content_types=",".join(struct.content_types) if struct.content_types else ""
        )
        db.add(new_struct)
        db.commit()
        db.refresh(new_struct)
        logger.info(f"Создана структура курса с ID: {new_struct.id}")
        return CourseStructureResponse(
            id=new_struct.id,
            sections=new_struct.sections,
            tests_per_section=new_struct.tests_per_section,
            lessons_per_section=new_struct.lessons_per_section,
            questions_per_test=new_struct.questions_per_test,
            final_test=new_struct.final_test,
            content_types=new_struct.content_types.split(",") if new_struct.content_types else []
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка создания структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка создания структуры курса")

@router.get("/course-structure/", summary="Получение всех структур курсов", response_model=List[CourseStructureResponse])
def get_all_course_structures(db: Session = Depends(get_db)):
    """
    Возвращает список всех структур курсов.
    """
    try:
        structs = db.query(CourseStructure).all()
        return [
            CourseStructureResponse(
                id=cs.id,
                sections=cs.sections,
                tests_per_section=cs.tests_per_section,
                lessons_per_section=cs.lessons_per_section,
                questions_per_test=cs.questions_per_test,
                final_test=cs.final_test,
                content_types=cs.content_types.split(",") if cs.content_types else []
            )
            for cs in structs
        ]
    except Exception as e:
        logger.error(f"Ошибка получения структур курсов: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения структур курсов")

@router.get("/course-structure/{cs_id}", summary="Получение структуры курса по ID", response_model=CourseStructureResponse)
def get_course_structure(cs_id: int, db: Session = Depends(get_db)):
    """
    Возвращает структуру курса по указанному ID.
    """
    try:
        cs = db.query(CourseStructure).filter(CourseStructure.id == cs_id).first()
        if not cs:
            raise HTTPException(status_code=404, detail="Структура курса не найдена")
        return CourseStructureResponse(
            id=cs.id,
            sections=cs.sections,
            tests_per_section=cs.tests_per_section,
            lessons_per_section=cs.lessons_per_section,
            questions_per_test=cs.questions_per_test,
            final_test=cs.final_test,
            content_types=cs.content_types.split(",") if cs.content_types else []
        )
    except Exception as e:
        logger.error(f"Ошибка получения структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка получения структуры курса")

@router.put("/course-structure/{cs_id}", summary="Обновление структуры курса", response_model=CourseStructureResponse)
def update_course_structure(cs_id: int, struct_update: CourseStructureUpdate, db: Session = Depends(get_db)):
    """
    Обновляет структуру курса по указанному ID.
    """
    try:
        cs = db.query(CourseStructure).filter(CourseStructure.id == cs_id).first()
        if not cs:
            raise HTTPException(status_code=404, detail="Структура курса не найдена")
        
        update_data = struct_update.dict(exclude_unset=True)
        if "content_types" in update_data and update_data["content_types"] is not None:
            update_data["content_types"] = ",".join(update_data["content_types"])
        
        for field, value in update_data.items():
            setattr(cs, field, value)
        
        db.commit()
        db.refresh(cs)
        logger.info(f"Обновлена структура курса с ID: {cs.id}")
        return CourseStructureResponse(
            id=cs.id,
            sections=cs.sections,
            tests_per_section=cs.tests_per_section,
            lessons_per_section=cs.lessons_per_section,
            questions_per_test=cs.questions_per_test,
            final_test=cs.final_test,
            content_types=cs.content_types.split(",") if cs.content_types else []
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
        cs = db.query(CourseStructure).filter(CourseStructure.id == cs_id).first()
        if not cs:
            raise HTTPException(status_code=404, detail="Структура курса не найдена")
        
        db.delete(cs)
        db.commit()
        logger.info(f"Удалена структура курса с ID: {cs_id}")
        return {"message": f"Структура курса с ID {cs_id} успешно удалена"}
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка удаления структуры курса: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка удаления структуры курса")
