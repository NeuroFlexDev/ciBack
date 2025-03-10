# app/routes/tasks.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.task import Task
from app.models.module import Module

router = APIRouter()

@router.post("/modules/{module_id}/tasks/")
def add_task(module_id: int, name: str, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    new_task = Task(name=name, module_id=module.id)
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return {"message": "Task added", "task": new_task}

@router.get("/modules/{module_id}/tasks/")
def get_tasks(module_id: int, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module.tasks
