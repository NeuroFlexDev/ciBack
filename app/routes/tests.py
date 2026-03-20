import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.test import Test
from app.models.user import User
from app.repositories.module import ModuleRepository
from app.repositories.test import TestRepository
from app.schemas.common import MessageResponse
from app.schemas.test import TestCreate, TestResponse, TestUpdate
from app.services.access_control import ensure_can_manage_module, ensure_can_manage_test
from app.services.auth import get_current_user_dep

router = APIRouter()


def build_test_response(test: Test) -> TestResponse:
    return TestResponse(
        id=test.id,
        question=test.question,
        answers=json.loads(test.answers or "[]"),
        correct=test.correct_answer,
        module_id=test.module_id,
        is_deleted=test.is_deleted,
    )


@router.post("/modules/{module_id}/tests/", summary="Добавить тест к модулю", response_model=TestResponse)
def add_test(
    module_id: int,
    payload: TestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    module = ModuleRepository.get_by_id(db, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    ensure_can_manage_module(user, module)
    try:
        new_test = TestRepository.add_test(db, module_id, payload)
        return build_test_response(new_test)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/modules/{module_id}/tests/", summary="Список тестов модуля", response_model=list[TestResponse])
def get_tests(module_id: int, db: Session = Depends(get_db)):
    try:
        return [build_test_response(test) for test in TestRepository.get_tests(db, module_id)]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tests/{test_id}", summary="Получить тест по ID", response_model=TestResponse)
def get_test(test_id: int, db: Session = Depends(get_db)):
    test = TestRepository.get_test(db, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")
    return build_test_response(test)


@router.put("/tests/{test_id}", summary="Обновить тест", response_model=TestResponse)
def update_test(
    test_id: int,
    payload: TestUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    test = TestRepository.get_test(db, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")
    ensure_can_manage_test(user, test)
    updated = TestRepository.update_test(db, test, payload)
    return build_test_response(updated)


@router.delete("/tests/{test_id}", summary="Удалить тест", response_model=MessageResponse)
def delete_test(
    test_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    test = TestRepository.get_test(db, test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")
    ensure_can_manage_test(user, test)
    TestRepository.delete_test(db, test)
    return MessageResponse(message="Test deleted")
