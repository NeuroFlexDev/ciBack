from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.course_version import CourseVersion
from app.models.lesson import Lesson
from app.models.lesson_version import LessonVersion
from app.models.module import Module
from app.models.module_version import ModuleVersion
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from app.models.user import User
from app.repositories.course import CourseRepository
from app.services.access_control import ensure_can_manage_course


def _active_items(items):
    return [item for item in items if not item.is_deleted]


def _serialize_course_snapshot(course: Course) -> dict:
    modules_payload = []
    for module in _active_items(course.modules):
        module_tasks = [
            {"name": task.name, "description": task.description}
            for task in _active_items(module.tasks)
            if task.lesson_id is None
        ]
        module_tests = [
            {
                "question": test.question,
                "answers": json.loads(test.answers or "[]"),
                "correct_answer": test.correct_answer,
            }
            for test in _active_items(module.tests)
            if test.lesson_id is None
        ]
        lessons_payload = []
        for lesson in _active_items(module.lessons):
            lesson_tasks = [
                {"name": task.name, "description": task.description}
                for task in _active_items(lesson.tasks)
            ]
            lesson_tests = [
                {
                    "question": test.question,
                    "answers": json.loads(test.answers or "[]"),
                    "correct_answer": test.correct_answer,
                }
                for test in _active_items(lesson.tests)
            ]
            lessons_payload.append(
                {
                    "title": lesson.title,
                    "description": lesson.description,
                    "theory": lesson.theory.content if lesson.theory and not lesson.theory.is_deleted else None,
                    "tasks": lesson_tasks,
                    "tests": lesson_tests,
                }
            )

        modules_payload.append(
            {
                "title": module.title,
                "tasks": module_tasks,
                "tests": module_tests,
                "lessons": lessons_payload,
            }
        )

    return {
        "course": {
            "name": course.name,
            "description": course.description,
            "level": course.level,
            "language": course.language,
        },
        "modules": modules_payload,
    }


def _soft_delete_course_tree(course: Course) -> None:
    for module in course.modules:
        if module.is_deleted:
            continue
        module.is_deleted = True
        for lesson in module.lessons:
            if lesson.is_deleted:
                continue
            lesson.is_deleted = True
            if lesson.theory and not lesson.theory.is_deleted:
                lesson.theory.is_deleted = True
            for task in lesson.tasks:
                if not task.is_deleted:
                    task.is_deleted = True
            for test in lesson.tests:
                if not test.is_deleted:
                    test.is_deleted = True
            for feedback in lesson.feedback:
                if not feedback.is_deleted:
                    feedback.is_deleted = True

        for task in module.tasks:
            if not task.is_deleted:
                task.is_deleted = True
        for test in module.tests:
            if not test.is_deleted:
                test.is_deleted = True


def create_course_snapshot(db: Session, *, course_id: int, user: User) -> CourseVersion:
    course = CourseRepository.get_by_id(db, course_id)
    if course is None:
        raise HTTPException(404, "Course not found")
    ensure_can_manage_course(user, course)

    snapshot = _serialize_course_snapshot(course)
    course_version = CourseVersion(
        course_id=course.id,
        name=course.name,
        description=course.description,
        level=course.level,
        language=course.language,
        snapshot_data=json.dumps(snapshot),
    )
    db.add(course_version)
    db.flush()

    for module in _active_items(course.modules):
        module_version = ModuleVersion(course_version_id=course_version.id, title=module.title)
        db.add(module_version)
        db.flush()
        for lesson in _active_items(module.lessons):
            db.add(
                LessonVersion(
                    module_version_id=module_version.id,
                    title=lesson.title,
                    description=lesson.description,
                )
            )

    course.current_version_id = course_version.id
    db.commit()
    db.refresh(course_version)
    return course_version


def restore_course_snapshot(db: Session, *, course_version_id: int, user: User) -> Course:
    course_version = (
        db.query(CourseVersion)
        .filter(CourseVersion.id == course_version_id, CourseVersion.is_deleted == False)
        .first()
    )
    if course_version is None:
        raise HTTPException(404, "Course version not found")
    if not course_version.snapshot_data:
        raise HTTPException(400, "This course version does not contain a restorable snapshot")

    course = db.query(Course).filter(Course.id == course_version.course_id, Course.is_deleted == False).first()
    if course is None:
        raise HTTPException(404, "Course not found")
    ensure_can_manage_course(user, course)

    snapshot = json.loads(course_version.snapshot_data)
    course_data = snapshot.get("course", {})
    course.name = course_data.get("name", course.name)
    course.description = course_data.get("description", course.description)
    course.level = course_data.get("level", course.level)
    course.language = course_data.get("language", course.language)

    _soft_delete_course_tree(course)

    for module_payload in snapshot.get("modules", []):
        module = Module(course_id=course.id, title=module_payload.get("title", "Untitled module"))
        db.add(module)
        db.flush()

        for task_payload in module_payload.get("tasks", []):
            db.add(
                Task(
                    module_id=module.id,
                    lesson_id=None,
                    name=task_payload.get("name", "Task"),
                    description=task_payload.get("description"),
                )
            )
        for test_payload in module_payload.get("tests", []):
            db.add(
                Test(
                    module_id=module.id,
                    lesson_id=None,
                    question=test_payload.get("question", ""),
                    answers=json.dumps(test_payload.get("answers", [])),
                    correct_answer=test_payload.get("correct_answer", ""),
                )
            )

        for lesson_payload in module_payload.get("lessons", []):
            lesson = Lesson(
                module_id=module.id,
                title=lesson_payload.get("title", "Untitled lesson"),
                description=lesson_payload.get("description"),
            )
            db.add(lesson)
            db.flush()

            theory_content = lesson_payload.get("theory")
            if theory_content:
                db.add(Theory(lesson_id=lesson.id, content=theory_content))

            for task_payload in lesson_payload.get("tasks", []):
                db.add(
                    Task(
                        module_id=module.id,
                        lesson_id=lesson.id,
                        name=task_payload.get("name", "Task"),
                        description=task_payload.get("description"),
                    )
                )
            for test_payload in lesson_payload.get("tests", []):
                db.add(
                    Test(
                        module_id=module.id,
                        lesson_id=lesson.id,
                        question=test_payload.get("question", ""),
                        answers=json.dumps(test_payload.get("answers", [])),
                        correct_answer=test_payload.get("correct_answer", ""),
                    )
                )

    course.current_version_id = course_version.id
    db.commit()
    db.refresh(course)
    return course
