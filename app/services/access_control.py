from __future__ import annotations

from fastapi import HTTPException, status

from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.task import Task
from app.models.test import Test
from app.models.theory import Theory
from app.models.user import User

ROLE_STUDENT = "student"
ROLE_LND = "lnd"
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"
PRIVILEGED_ROLES = {ROLE_EDITOR, ROLE_ADMIN}
TEMPLATE_MANAGER_ROLES = {ROLE_LND, ROLE_EDITOR, ROLE_ADMIN}


def is_privileged_user(user: User) -> bool:
    return (user.role or ROLE_STUDENT) in PRIVILEGED_ROLES


def can_manage_global_templates(user: User) -> bool:
    return (user.role or ROLE_STUDENT) in TEMPLATE_MANAGER_ROLES


def ensure_can_manage_templates(user: User) -> None:
    if can_manage_global_templates(user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only L&D, editor, or admin users can modify course structures",
    )


def can_manage_course(user: User, course: Course) -> bool:
    if is_privileged_user(user):
        return True
    return course.owner_id == user.id


def ensure_can_manage_course(user: User, course: Course) -> None:
    if can_manage_course(user, course):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to modify this course",
    )


def ensure_can_manage_module(user: User, module: Module) -> None:
    if module.course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    ensure_can_manage_course(user, module.course)


def ensure_can_manage_lesson(user: User, lesson: Lesson) -> None:
    if lesson.module is None or lesson.module.course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    ensure_can_manage_course(user, lesson.module.course)


def ensure_can_manage_theory(user: User, theory: Theory) -> None:
    if theory.lesson is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found")
    ensure_can_manage_lesson(user, theory.lesson)


def ensure_can_manage_task(user: User, task: Task) -> None:
    if task.module is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    ensure_can_manage_module(user, task.module)


def ensure_can_manage_test(user: User, test: Test) -> None:
    if test.module is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Module not found")
    ensure_can_manage_module(user, test.module)
