# app/routes/tasks.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse
from app.repositories.task import TaskRepository
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.course_editor_service import CourseEditorService
from app.services.course_publication_service import CoursePublicationService

router = APIRouter()

@router.post("/modules/{module_id}/tasks/", summary="Добавить задачу к модулю", response_model=TaskResponse)
def add_task(module_id: int, payload: TaskCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    module = CourseEditorService._module(db, module_id, user.id, True)
    CoursePublicationService.prepare_for_edit(module.course)
    try:
        task = TaskRepository.add_task(db, module_id, payload)
        return TaskResponse.model_validate(task)
    except ValueError as e:
        raise HTTPException(404, detail=str(e))

@router.get("/modules/{module_id}/tasks/", summary="Получить список задач модуля", response_model=list[TaskResponse])
def get_tasks(module_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    CourseEditorService._module(db, module_id, user.id)
    try:
        tasks = TaskRepository.get_tasks(db, module_id)
        return [TaskResponse.model_validate(t) for t in tasks if not t.is_deleted]
    except ValueError as e:
        raise HTTPException(404, detail=str(e))

@router.get("/tasks/{task_id}", summary="Получить задачу по ID", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    CourseEditorService._module(db, task.module_id, user.id)
    return TaskResponse.model_validate(task)

@router.put("/tasks/{task_id}", summary="Обновить задачу", response_model=TaskResponse)
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    module = CourseEditorService._module(db, task.module_id, user.id, True)
    db.refresh(task)
    CoursePublicationService.prepare_for_edit(module.course)
    task = TaskRepository.update_task(db, task, payload)
    return TaskResponse.model_validate(task)

@router.delete("/tasks/{task_id}", summary="Удалить задачу")
def delete_task(task_id: int, expected_revision:int=Query(gt=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = TaskRepository.get_task(db, task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    module = CourseEditorService._module(db, task.module_id, user.id, True)
    db.refresh(task)
    if task.revision != expected_revision:
        raise HTTPException(409, {'message':'Задание изменено другим редактором','current_revision':task.revision})
    CoursePublicationService.prepare_for_edit(module.course)
    TaskRepository.delete_task(db, task)
    return {"message": "Task deleted"}
