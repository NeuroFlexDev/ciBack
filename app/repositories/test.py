import json
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.module import Module
from app.models.test import Test
from app.schemas.test import TestCreate, TestUpdate


class TestRepository:
    @staticmethod
    def add_test(db: Session, module_id: int, payload: TestCreate) -> Test:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found")

        new_test = Test(
            module_id=module.id,
            lesson_id=None,
            question=payload.question,
            answers=json.dumps(payload.answers),
            correct_answer=payload.correct,
        )
        db.add(new_test)
        db.commit()
        db.refresh(new_test)
        return new_test

    @staticmethod
    def get_tests(db: Session, module_id: int) -> List[Test]:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError("Module not found")

        return (
            db.query(Test)
            .filter(Test.module_id == module_id, Test.is_deleted == False)
            .order_by(Test.id.asc())
            .all()
        )

    @staticmethod
    def get_test(db: Session, test_id: int) -> Optional[Test]:
        return db.query(Test).filter(Test.id == test_id, Test.is_deleted == False).first()

    @staticmethod
    def update_test(db: Session, test: Test, payload: TestUpdate) -> Test:
        if payload.question is not None:
            test.question = payload.question
        if payload.answers is not None:
            test.answers = json.dumps(payload.answers)
        if payload.correct is not None:
            test.correct_answer = payload.correct
        db.commit()
        db.refresh(test)
        return test

    @staticmethod
    def delete_test(db: Session, test: Test) -> None:
        test.is_deleted = True
        db.commit()
