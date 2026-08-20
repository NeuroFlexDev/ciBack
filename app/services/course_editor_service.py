from __future__ import annotations

import json
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.lesson import Lesson
from app.models.lesson_version import LessonVersion
from app.models.module import Module
from app.models.module_version import ModuleVersion
from app.models.test import Test
from app.models.test_version import TestVersion
from app.models.theory import Theory
from app.schemas.course_editor import OrderItem
from app.services.course_publication_service import CoursePublicationService


def _not_found(label: str = "Object") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label} not found")


def _conflict(expected: int, current: int) -> HTTPException:
    return HTTPException(status_code=409, detail={"message": "Revision conflict", "expected_revision": expected, "current_revision": current})


class CourseEditorService:
    @staticmethod
    def _course(db: Session, course_id: int, owner_id: int, lock: bool = False) -> Course:
        query = db.query(Course).filter(Course.id == course_id, Course.owner_id == owner_id, Course.is_deleted.is_(False))
        course = (query.with_for_update() if lock else query).first()
        if course is None:
            raise _not_found("Course")
        if course.status == "generating":
            raise HTTPException(status_code=409, detail="Course is being generated")
        return course

    @staticmethod
    def _module(db: Session, module_id: int, owner_id: int, lock: bool = False) -> Module:
        query = db.query(Module).join(Course).filter(Module.id == module_id, Module.is_deleted.is_(False), Course.owner_id == owner_id, Course.is_deleted.is_(False))
        module = (query.with_for_update() if lock else query).first()
        if module is None:
            raise _not_found("Module")
        CourseEditorService._course(db, module.course_id, owner_id)
        return module

    @staticmethod
    def _lesson(db: Session, lesson_id: int, owner_id: int, lock: bool = False) -> Lesson:
        query = db.query(Lesson).join(Module).join(Course).filter(Lesson.id == lesson_id, Lesson.is_deleted.is_(False), Module.is_deleted.is_(False), Course.owner_id == owner_id, Course.is_deleted.is_(False))
        lesson = (query.with_for_update() if lock else query).first()
        if lesson is None:
            raise _not_found("Lesson")
        CourseEditorService._course(db, lesson.module.course_id, owner_id)
        return lesson

    @staticmethod
    def _test(db: Session, test_id: int, owner_id: int, lock: bool = False) -> Test:
        query = db.query(Test).outerjoin(Module, Test.module_id == Module.id).join(Course, func.coalesce(Module.course_id, Test.course_id) == Course.id).filter(Test.id == test_id, Test.is_deleted.is_(False), Course.owner_id == owner_id, Course.is_deleted.is_(False))
        test = (query.with_for_update() if lock else query).first()
        if test is None:
            raise _not_found("Test")
        CourseEditorService._course(db, test.module.course_id if test.module_id else test.course_id, owner_id)
        return test

    @staticmethod
    def _check(entity, expected: int) -> None:
        if entity.revision != expected:
            raise _conflict(expected, entity.revision)

    @staticmethod
    def _module_snapshot(db: Session, item: Module, owner_id: int, deleted: bool = False) -> None:
        db.add(ModuleVersion(module_id=item.id, revision=item.revision, title=item.title, description=item.description, position=item.position, deleted=deleted, created_by=owner_id))

    @staticmethod
    def _lesson_snapshot(db: Session, item: Lesson, owner_id: int, deleted: bool = False) -> None:
        content = item.theory.content if item.theory and not item.theory.is_deleted else ""
        db.add(LessonVersion(lesson_id=item.id, revision=item.revision, title=item.title, description=item.description, content=content, position=item.position, deleted=deleted, created_by=owner_id))

    @staticmethod
    def _test_snapshot(db: Session, item: Test, owner_id: int, deleted: bool = False) -> None:
        db.add(TestVersion(test_id=item.id, revision=item.revision, assessment_scope=item.assessment_scope, module_id=item.module_id, course_id=item.course_id, question=item.question, answers=item.answers or "[]", correct_answer=item.correct_answer, position=item.position, deleted=deleted, created_by=owner_id))

    @staticmethod
    def create_module(db: Session, course_id: int, owner_id: int, title: str, description: str = "") -> Module:
        course = CourseEditorService._course(db, course_id, owner_id, True)
        CoursePublicationService.prepare_for_edit(course)
        position = db.query(func.coalesce(func.max(Module.position), -1)).filter(Module.course_id == course_id, Module.is_deleted.is_(False)).scalar() + 1
        item = Module(course_id=course_id, title=title, description=description, position=position, revision=1)
        db.add(item); db.flush(); CourseEditorService._module_snapshot(db, item, owner_id); db.commit(); db.refresh(item)
        return item

    @staticmethod
    def update_module(db: Session, module_id: int, owner_id: int, title: str | None, description: str | None, expected: int) -> Module:
        item = CourseEditorService._module(db, module_id, owner_id, True); CourseEditorService._check(item, expected)
        CoursePublicationService.prepare_for_edit(item.course)
        if title is not None: item.title = title
        if description is not None: item.description = description
        item.revision += 1; CourseEditorService._module_snapshot(db, item, owner_id); db.commit(); db.refresh(item); return item

    @staticmethod
    def create_lesson(db: Session, course_id: int, module_id: int, owner_id: int, title: str, description: str, content: str) -> Lesson:
        module = CourseEditorService._module(db, module_id, owner_id, True)
        if module.course_id != course_id: raise _not_found("Module")
        CoursePublicationService.prepare_for_edit(module.course)
        position = db.query(func.coalesce(func.max(Lesson.position), -1)).filter(Lesson.module_id == module_id, Lesson.is_deleted.is_(False)).scalar() + 1
        item = Lesson(module_id=module_id, title=title, description=description, position=position, revision=1)
        db.add(item); db.flush(); db.add(Theory(lesson_id=item.id, content=content)); db.flush(); CourseEditorService._lesson_snapshot(db, item, owner_id); db.commit(); db.refresh(item); return item

    @staticmethod
    def update_lesson(db: Session, lesson_id: int, owner_id: int, data: dict, expected: int) -> Lesson:
        item = CourseEditorService._lesson(db, lesson_id, owner_id, True); CourseEditorService._check(item, expected)
        CoursePublicationService.prepare_for_edit(item.module.course)
        for field in ("title", "description"):
            if field in data and data[field] is not None: setattr(item, field, data[field])
        if "content" in data and data["content"] is not None:
            if item.theory: item.theory.content, item.theory.is_deleted = data["content"], False
            else: db.add(Theory(lesson_id=item.id, content=data["content"]))
        item.revision += 1; db.flush(); CourseEditorService._lesson_snapshot(db, item, owner_id); db.commit(); db.refresh(item); return item

    @staticmethod
    def create_test(db: Session, owner_id: int, *, module_id: int | None, course_id: int | None, question: str, answers: list[str], correct: str) -> Test:
        if module_id is not None:
            parent = CourseEditorService._module(db, module_id, owner_id, True); scope = "module"; parent_course = None
            CoursePublicationService.prepare_for_edit(parent.course)
            query = db.query(func.coalesce(func.max(Test.position), -1)).filter(Test.module_id == module_id, Test.is_deleted.is_(False))
        else:
            course = CourseEditorService._course(db, course_id, owner_id, True); parent = None; scope = "final"; parent_course = course_id
            CoursePublicationService.prepare_for_edit(course)
            query = db.query(func.coalesce(func.max(Test.position), -1)).filter(Test.course_id == course_id, Test.is_deleted.is_(False))
        item = Test(module_id=parent.id if parent else None, course_id=parent_course, assessment_scope=scope, position=query.scalar() + 1, revision=1, question=question, answers=json.dumps(answers, ensure_ascii=False), correct_answer=correct)
        db.add(item); db.flush(); CourseEditorService._test_snapshot(db, item, owner_id); db.commit(); db.refresh(item); return item

    @staticmethod
    def update_test(db: Session, test_id: int, owner_id: int, data: dict, expected: int) -> Test:
        item = CourseEditorService._test(db, test_id, owner_id, True); CourseEditorService._check(item, expected)
        CoursePublicationService.prepare_for_edit(item.module.course if item.module_id else item.course)
        item.question, item.answers, item.correct_answer = data["question"], json.dumps(data["answers"], ensure_ascii=False), data["correct_answer"]
        item.revision += 1; CourseEditorService._test_snapshot(db, item, owner_id); db.commit(); db.refresh(item); return item

    @staticmethod
    def reorder(db: Session, *, entity, parent_filter, items: Iterable[OrderItem], owner_id: int, snapshot) -> list:
        rows = db.query(entity).filter(*parent_filter, entity.is_deleted.is_(False)).with_for_update().all()
        by_id = {row.id: row for row in rows}; given = list(items)
        if set(by_id) != {item.id for item in given}: raise HTTPException(status_code=422, detail="items must match all active children")
        for request in given: CourseEditorService._check(by_id[request.id], request.expected_revision)
        if rows:
            first = rows[0]
            course = first.course if entity is Module else first.module.course
            CoursePublicationService.prepare_for_edit(course)
        for request in given:
            row = by_id[request.id]; row.position = request.position; row.revision += 1; snapshot(db, row, owner_id)
        db.commit(); return sorted(rows, key=lambda row: row.position)

    @staticmethod
    def delete(db: Session, *, kind: str, item_id: int, owner_id: int, expected: int) -> None:
        if kind == "test":
            item = CourseEditorService._test(db, item_id, owner_id, True); CourseEditorService._check(item, expected); item.is_deleted = True; item.revision += 1; CourseEditorService._test_snapshot(db, item, owner_id, True)
            CoursePublicationService.prepare_for_edit(item.module.course if item.module_id else item.course)
        elif kind == "lesson":
            item = CourseEditorService._lesson(db, item_id, owner_id, True); CourseEditorService._check(item, expected); item.is_deleted = True; item.revision += 1
            CoursePublicationService.prepare_for_edit(item.module.course)
            if item.theory: item.theory.is_deleted = True
            CourseEditorService._lesson_snapshot(db, item, owner_id, True)
        else:
            item = CourseEditorService._module(db, item_id, owner_id, True); CourseEditorService._check(item, expected); item.is_deleted = True; item.revision += 1
            CoursePublicationService.prepare_for_edit(item.course)
            for lesson in item.lessons:
                if not lesson.is_deleted:
                    lesson.is_deleted = True; lesson.revision += 1
                    if lesson.theory: lesson.theory.is_deleted = True
                    CourseEditorService._lesson_snapshot(db, lesson, owner_id, True)
            for test in item.tests:
                if not test.is_deleted: test.is_deleted = True; test.revision += 1; CourseEditorService._test_snapshot(db, test, owner_id, True)
            for task in item.tasks: task.is_deleted = True
            CourseEditorService._module_snapshot(db, item, owner_id, True)
        db.commit()
