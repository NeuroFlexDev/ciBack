# app/routes/tests.py
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.module import Module
from app.models.test import Test

router = APIRouter()


class TestCreate(BaseModel):
    test: str
    description: str


class TestUpdate(BaseModel):
    question: str
    answers: list[str]
    correct: str


@router.post("/modules/{module_id}/tests/", summary="Добавить тест к модулю")
def add_test(module_id: int, payload: TestCreate, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(404, detail="Module not found")

    desc = payload.description
    if "Варианты:" in desc and "(Правильный:" in desc:
        parts = desc.split("Варианты:")[1].split("(Правильный:")
        answers = parts[0].strip().split(", ")
        correct = parts[1].replace(")", "").strip()
    else:
        raise HTTPException(400, detail="Неверный формат описания теста")

    new_test = Test(
        module_id=module.id,
        question=payload.test,
        answers=json.dumps(answers),
        correct_answer=correct,
    )
    db.add(new_test)
    db.commit()
    db.refresh(new_test)
    return {"message": "Test added", "test": new_test}


@router.get("/modules/{module_id}/tests/", summary="Получить тесты модуля")
def get_tests(module_id: int, db: Session = Depends(get_db)):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(404, detail="Module not found")
    return module.tests


@router.get("/tests/{test_id}", summary="Получить тест по ID")
def get_test(test_id: int, db: Session = Depends(get_db)):
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        raise HTTPException(404, detail="Test not found")
    return {
        "id": test.id,
        "question": test.question,
        "answers": json.loads(test.answers),
        "correct": test.correct_answer,
    }


@router.put("/tests/{test_id}", summary="Обновить тест")
def update_test(test_id: int, payload: TestUpdate, db: Session = Depends(get_db)):
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        raise HTTPException(404, detail="Test not found")

    test.question = payload.question
    test.answers = json.dumps(payload.answers)
    test.correct_answer = payload.correct
    db.commit()
    return {"message": "Test updated", "test": test}


@router.delete("/tests/{test_id}", summary="Удалить тест")
def delete_test(test_id: int, db: Session = Depends(get_db)):
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        raise HTTPException(404, detail="Test not found")
    db.delete(test)
    db.commit()
    return {"message": "Test deleted"}
