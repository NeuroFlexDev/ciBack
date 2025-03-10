# app/routes/modules.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from app.database.db import get_db
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.task import Task
from app.models.test import Test
from app.models.course import Course

router = APIRouter()

class LessonIn(BaseModel):
    lesson: str
    description: str

class TestIn(BaseModel):
    test: str
    description: str

class TaskIn(BaseModel):
    name: str

class ModuleIn(BaseModel):
    title: str
    lessons: List[LessonIn] = []
    tests: List[TestIn] = []
    tasks: List[TaskIn] = []

@router.post("/courses/{course_id}/modules/bulk_save")
def bulk_save_modules(course_id: int, modules: List[ModuleIn], db: Session = Depends(get_db)):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(404, detail="Course not found")
    
    # Очищаем старые модули (по желанию) или обновляем существующие
    # В данном примере - просто удалим все и перезапишем
    for mod in course.modules:
        db.delete(mod)
    db.commit()

    # Создаем новые
    for m in modules:
        new_mod = Module(title=m.title, course_id=course.id)
        db.add(new_mod)
        db.commit()
        db.refresh(new_mod)

        # Уроки
        for l in m.lessons:
            new_lesson = Lesson(
                lesson=l.lesson,
                description=l.description,
                module_id=new_mod.id
            )
            db.add(new_lesson)
        # Тесты
        for t in m.tests:
            new_test = Test(
                test=t.test,
                description=t.description,
                module_id=new_mod.id
            )
            db.add(new_test)
        # Задачи
        for tsk in m.tasks:
            new_task = Task(name=tsk.name, module_id=new_mod.id)
            db.add(new_task)

        db.commit()

    return {"message": "Modules saved successfully"}
