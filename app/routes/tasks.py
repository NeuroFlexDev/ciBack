# app/routes/tasks.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse
from app.repositories.task import TaskRepository

router = APIRouter()

@router.post("/modules/{module_id}/tasks/", summary="Добавить задачу к модулю", response_model=TaskResponse)
def add_task(module_id: int, payload: TaskCreate, db: Session = Depends(get_db)):
    try:
        task = TaskRepository.add_task(db, module_id, payload)
        return TaskResponse(id=task.id, name=task.name, description=task.description, module_id=task.module_id)
    except ValueError as e:
        raise HTTPException(404, detail=str(e))

@router.get("/modules/{module_id}/tasks/", summary="Получить список задач модуля", response_model=list[TaskResponse])
def get_tasks(module_id: int, db: Session = Depends(get_db)):
    try:
        tasks = TaskRepository.get_tasks(db, module_id)
        return [TaskResponse(id=t.id, name=t.name, description=t.description, module_id=t.module_id) for t in tasks]
    except ValueError as e:
        raise HTTPException(404, detail=str(e))

@router.get("/tasks/{task_id}", summary="Получить задачу по ID", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    return TaskResponse(id=task.id, name=task.name, description=task.description, module_id=task.module_id)

@router.put("/tasks/{task_id}", summary="Обновить задачу", response_model=TaskResponse)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    task = TaskRepository.update_task(db, task, payload)
    return TaskResponse(id=task.id, name=task.name, description=task.description, module_id=task.module_id)

@router.delete("/tasks/{task_id}", summary="Удалить задачу")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    TaskRepository.delete_task(db, task)
    return {"message": "Task deleted"}
