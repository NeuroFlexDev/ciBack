import json
from sqlalchemy.orm import Session
from app.models.module import Module
from app.models.test import Test
from app.schemas.test import TestCreate, TestUpdate
from typing import List, Optional

class TestRepository:
    @staticmethod
    def add_test(db: Session, module_id: int, payload: TestCreate) -> Test:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError('Module not found')
        desc = payload.description
        if "Варианты:" in desc and "(Правильный:" in desc:
            parts = desc.split("Варианты:")[1].split("(Правильный:")
            answers = parts[0].strip().split(", ")
            correct = parts[1].replace(")", "").strip()
        else:
            raise ValueError("Неверный формат описания теста")
        new_test = Test(
            module_id=module.id,
            question=payload.test,
            answers=json.dumps(answers),
            correct_answer=correct
        )
        db.add(new_test)
        db.commit()
        db.refresh(new_test)
        return new_test

    @staticmethod
    def get_tests(db: Session, module_id: int) -> List[Test]:
        module = db.query(Module).filter(Module.id == module_id, Module.is_deleted == False).first()
        if not module:
            raise ValueError('Module not found')
        return module.tests

    @staticmethod
    def get_test(db: Session, test_id: int) -> Optional[Test]:
        return db.query(Test).filter(Test.id == test_id, Test.is_deleted == False).first()

    @staticmethod
    def update_test(db: Session, test: Test, payload: TestUpdate) -> Test:
        test.question = payload.question
        test.answers = json.dumps(payload.answers)
        test.correct_answer = payload.correct
        db.commit()
        db.refresh(test)
        return test

    @staticmethod
    def delete_test(db: Session, test: Test):
        test.is_deleted = True
        # db.delete(test)
        db.commit()
