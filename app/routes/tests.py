# app/routes/tests.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.test import Test
from app.models.module import Module

router = APIRouter()

@router.post("/modules/{module_id}/tests/")
def add_test(module_id: int, test: str, description: str, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    new_test = Test(test=test, description=description, module_id=module.id)
    db.add(new_test)
    db.commit()
    db.refresh(new_test)
    return {"message": "Test added", "test": new_test}

@router.get("/modules/{module_id}/tests/")
def get_tests(module_id: int, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return module.tests
