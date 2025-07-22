# app/routes/tasks.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.module import Module
from app.models.task import Task

router = APIRouter()


class TaskCreate(BaseModel):
    name: str
    description: str = ""


class TaskUpdate(BaseModel):
    name: str
    description: str = ""


@router.post("/modules/{module_id}/tasks/", summary="Добавить задачу к модулю")
def add_task(module_id: int, payload: TaskCreate, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(404, detail="Module not found")

    task = Task(name=payload.name, description=payload.description, module_id=module.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return {"message": "Task added", "task": task}


@router.get("/modules/{module_id}/tasks/", summary="Получить список задач модуля")
def get_tasks(module_id: int, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(404, detail="Module not found")
    return module.tasks


@router.get("/tasks/{task_id}", summary="Получить задачу по ID")
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, detail="Task not found")
    return task


@router.put("/tasks/{task_id}", summary="Обновить задачу")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, detail="Task not found")

    task.name = payload.name
    task.description = payload.description
    db.commit()
    return {"message": "Task updated", "task": task}


@router.delete("/tasks/{task_id}", summary="Удалить задачу")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"message": "Task deleted"}
