from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.repositories.task import TaskRepository
from app.schemas.common import MessageResponse
from app.schemas.task import TaskCreate, TaskResponse, TaskUpdate

router = APIRouter()


@router.post("/modules/{module_id}/tasks/", summary="Добавить задачу к модулю", response_model=TaskResponse)
def add_task(module_id: int, payload: TaskCreate, db: Session = Depends(get_db)):
    try:
        return TaskRepository.add_task(db, module_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/modules/{module_id}/tasks/", summary="Список задач модуля", response_model=list[TaskResponse])
def get_tasks(module_id: int, db: Session = Depends(get_db)):
    try:
        return TaskRepository.get_tasks(db, module_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/tasks/{task_id}", summary="Получить задачу по ID", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/tasks/{task_id}", summary="Обновить задачу", response_model=TaskResponse)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskRepository.update_task(db, task, payload)


@router.delete("/tasks/{task_id}", summary="Удалить задачу", response_model=MessageResponse)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    TaskRepository.delete_task(db, task)
    return MessageResponse(message="Task deleted")
