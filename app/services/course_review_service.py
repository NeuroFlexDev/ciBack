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
    return {
        "id": item.id,
        "course_id": item.course_id,
        "title": item.title,
        "position": item.position,
        "updated_at": item.updated_at,
        "revision": item.revision,
        "lessons": [_lesson_out(lesson) for lesson in lessons],
        "tests": [_test_out(test) for test in tests],
    }


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
        duration = sum(lesson["estimated_duration_minutes"] for lesson in lessons)
        duration += question_count * MINUTES_PER_TEST_QUESTION
        return {
            "course": {
                "id": course.id,
                "title": course.name or "",
                "description": course.description,
                "difficulty": course.level,
                "language": course.language,
                "status": course.status,
                "updated_at": course.updated_at,
            },
            "metrics": {
                "module_count": len(module_payload),
                "lesson_count": len(lessons),
                "test_question_count": question_count,
                "estimated_duration_minutes": duration,
            },
            "modules": module_payload,
            "final_tests": [_test_out(test) for test in final_tests],
        }

    @staticmethod
    def module_detail(db: Session, module_id: int, owner_id: int) -> dict:
        module = CourseReviewRepository.owned_module(db, module_id, owner_id)
        if module is None:
            raise HTTPException(status_code=404, detail="Module not found")
        return _module_out(module)
