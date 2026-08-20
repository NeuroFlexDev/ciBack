from __future__ import annotations

import json
import math
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.test import Test
from app.models.theory import Theory
from app.repositories.course_review import CourseReviewRepository


WORDS_PER_MINUTE = 200
MIN_LESSON_MINUTES = 1
MINUTES_PER_TEST_QUESTION = 2


def _answers(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _lesson_minutes(content: str, description: str | None) -> int:
    words = len(re.findall(r"\w+", content or description or ""))
    return max(MIN_LESSON_MINUTES, math.ceil(words / WORDS_PER_MINUTE))


def _test_out(item: Test) -> dict:
    return {
        "id": item.id,
        "question": item.question,
        "answers": _answers(item.answers),
        "correct_answer": item.correct_answer,
        "position": item.position,
        "updated_at": item.updated_at,
        "revision": item.revision,
    }


def _lesson_out(item: Lesson) -> dict:
    content = item.theory.content if item.theory and not item.theory.is_deleted else ""
    return {
        "id": item.id,
        "module_id": item.module_id,
        "title": item.title,
        "description": item.description,
        "content": content,
        "position": item.position,
        "estimated_duration_minutes": _lesson_minutes(content, item.description),
        "updated_at": item.updated_at,
        "revision": item.revision,
    }


def _module_out(item: Module) -> dict:
    lessons = sorted(
        (lesson for lesson in item.lessons if not lesson.is_deleted),
        key=lambda lesson: (lesson.position, lesson.id),
    )
    tests = sorted(
        (test for test in item.tests if not test.is_deleted),
        key=lambda test: (test.position, test.id),
    )
    tasks = sorted(
        (task for task in item.tasks if not task.is_deleted),
        key=lambda task: task.id,
    )
    return {
        "id": item.id,
        "course_id": item.course_id,
        "title": item.title,
        "description": item.description or "",
        "status": _module_status(item),
        "position": item.position,
        "updated_at": item.updated_at,
        "revision": item.revision,
        "lessons": [_lesson_out(lesson) for lesson in lessons],
        "tests": [_test_out(test) for test in tests],
        "tasks": [
            {
                "id": task.id,
                "module_id": task.module_id,
                "name": task.name,
                "description": task.description or "",
                "updated_at": task.updated_at,
            }
            for task in tasks
        ],
    }


def _course_status(course: Course) -> str:
    if course.publication_status == "published":
        return "published"
    return "ready" if course.status == "ready" else "draft"


def _module_status(module: Module) -> str:
    if module.course.publication_status == "published":
        return "published"
    has_ready_lesson = any(
        not lesson.is_deleted
        and lesson.theory is not None
        and not lesson.theory.is_deleted
        and bool((lesson.theory.content or "").strip())
        for lesson in module.lessons
    )
    return "ready" if has_ready_lesson else "draft"


class CourseReviewService:
    @staticmethod
    def structure(db: Session, course_id: int, owner_id: int) -> dict:
        course = CourseReviewRepository.owned_course(db, course_id, owner_id)
        if course is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        modules = sorted(
            (module for module in course.modules if not module.is_deleted),
            key=lambda module: (module.position, module.id),
        )
        module_payload = [_module_out(module) for module in modules]
        final_tests = sorted(
            (test for test in course.final_tests if not test.is_deleted),
            key=lambda test: (test.position, test.id),
        )
        lessons = [lesson for module in module_payload for lesson in module["lessons"]]
        question_count = sum(len(module["tests"]) for module in module_payload) + len(final_tests)
        task_count = sum(len(module["tasks"]) for module in module_payload)
        duration = sum(lesson["estimated_duration_minutes"] for lesson in lessons)
        duration += question_count * MINUTES_PER_TEST_QUESTION
        timeline = []
        for module in module_payload:
            module_duration = sum(
                lesson["estimated_duration_minutes"] for lesson in module["lessons"]
            ) + len(module["tests"]) * MINUTES_PER_TEST_QUESTION
            timeline.append(
                {
                    "module_id": module["id"],
                    "order": module["position"] + 1,
                    "title": module["title"],
                    "description": module["description"],
                    "lessons_count": len(module["lessons"]),
                    "tests_count": len(module["tests"]),
                    "tasks_count": len(module["tasks"]),
                    "estimated_time_minutes": module_duration,
                    "status": module["status"],
                }
            )
        return {
            "course_id": course.id,
            "title": course.name or "",
            "description": course.description or "",
            "status": _course_status(course),
            "metrics": {
                "modules_count": len(module_payload),
                "lessons_count": len(lessons),
                "tests_count": question_count,
                "tasks_count": task_count,
                "estimated_time_minutes": duration,
            },
            "modules_timeline": timeline,
        }

    @staticmethod
    def module_detail(db: Session, module_id: int, owner_id: int) -> dict:
        module = CourseReviewRepository.owned_module(db, module_id, owner_id)
        if module is None:
            raise HTTPException(status_code=404, detail="Module not found")
        return _module_out(module)
