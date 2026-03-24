from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.module import Module
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskUpdate


class TaskRepository:
    @staticmethod
    def add_task(db: Session, module_id: int, payload: TaskCreate) -> Task:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found")

        task = Task(
            name=payload.name,
            description=payload.description,
            module_id=module.id,
            lesson_id=None,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get_tasks(db: Session, module_id: int) -> List[Task]:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found")

        return (
            db.query(Task)
            .filter(Task.module_id == module_id, Task.is_deleted == False)
            .order_by(Task.id.asc())
            .all()
        )

    @staticmethod
    def get_task(db: Session, task_id: int) -> Optional[Task]:
        return db.query(Task).filter(Task.id == task_id, Task.is_deleted == False).first()

    @staticmethod
    def update_task(db: Session, task: Task, payload: TaskUpdate) -> Task:
        if payload.name is not None:
            task.name = payload.name
        if payload.description is not None:
            task.description = payload.description
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete_task(db: Session, task: Task) -> None:
        task.is_deleted = True
        db.commit()
