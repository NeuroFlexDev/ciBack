from sqlalchemy.orm import Session
from app.models.module import Module
from app.models.task import Task
from typing import Optional, List

class TaskRepository:
    @staticmethod
    def add_task(db: Session, module_id: int, payload) -> Task:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found")
        task = Task(name=payload.name, description=payload.description, module_id=module.id)
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get_tasks(db: Session, module_id: int) -> List[Task]:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found")
        return module.tasks

    @staticmethod
    def get_task(db: Session, task_id: int) -> Optional[Task]:
        return db.query(Task).filter(Task.id == task_id, Task.is_deleted == False).first()

    @staticmethod
    def update_task(db: Session, task: Task, payload) -> Task:
        task.name = payload.name
        task.description = payload.description
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete_task(db: Session, task: Task):
        task.is_deleted = True
        # db.delete(task)
        db.commit()
