# app/routes/tests.py
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.schemas.test import TestCreate, TestUpdate, TestResponse
from app.repositories.test import TestRepository

router = APIRouter()

@router.post("/modules/{module_id}/tests/", summary="Добавить тест к модулю", response_model=TestResponse)
def add_test(module_id: int, payload: TestCreate, db: Session = Depends(get_db)):
    try:
        new_test = TestRepository.add_test(db, module_id, payload)
        return TestResponse(
            id=new_test.id,
            question=new_test.question,
            answers=json.loads(new_test.answers),
            correct=new_test.correct_answer,
            module_id=new_test.module_id
        )
    except ValueError as e:
        raise HTTPException(400 if "формат" in str(e) else 404, detail=str(e))

@router.get("/modules/{module_id}/tests/", summary="Получить тесты модуля", response_model=list[TestResponse])
def get_tests(module_id: int, db: Session = Depends(get_db)):
    try:
        tests = TestRepository.get_tests(db, module_id)
        return [TestResponse(
            id=t.id,
            question=t.question,
            answers=json.loads(t.answers),
            correct=t.correct_answer,
            module_id=t.module_id
        ) for t in tests]
    except ValueError as e:
        raise HTTPException(404, detail=str(e))

@router.get("/tests/{test_id}", summary="Получить тест по ID", response_model=TestResponse)
def get_test(test_id: int, db: Session = Depends(get_db)):
    test = TestRepository.get_test(db, test_id)
    if not test:
        raise HTTPException(404, detail="Test not found")
    return TestResponse(
        id=test.id,
        question=test.question,
        answers=json.loads(test.answers),
        correct=test.correct_answer,
        module_id=test.module_id
    )

@router.put("/tests/{test_id}", summary="Обновить тест", response_model=TestResponse)
def update_test(test_id: int, payload: TestUpdate, db: Session = Depends(get_db)):
    test = TestRepository.get_test(db, test_id)
    if not test:
        raise HTTPException(404, detail="Test not found")
    test = TestRepository.update_test(db, test, payload)
    return TestResponse(
        id=test.id,
        question=test.question,
        answers=json.loads(test.answers),
        correct=test.correct_answer,
        module_id=test.module_id
    )

@router.delete("/tests/{test_id}", summary="Удалить тест")
def delete_test(test_id: int, db: Session = Depends(get_db)):
    test = TestRepository.get_test(db, test_id)
    if not test:
        raise HTTPException(404, detail="Test not found")
    TestRepository.delete_test(db, test)
    return {"message": "Test deleted"}
