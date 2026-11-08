# tests/factories.py
import json
from dataclasses import fields, is_dataclass

from app.models.course import Course
from app.models.course_structure import CourseStructure
from app.models.lesson import Lesson
from app.models.module import Module


def unpack_call_options(kwargs):
    """Let recording fakes inspect semantic fields of typed invocation objects.

    The production boundary receives the objects unchanged. This view keeps
    assertions about model choice, retries and token limits explicit in tests.
    """
    result = dict(kwargs)
    for name in ("request", "options"):
        value = result.get(name)
        if value is None or not is_dataclass(value):
            continue
        result.pop(name)
        result.update(
            {
                field.name: getattr(value, field.name)
                for field in fields(value)
                if getattr(value, field.name) is not None
            }
        )
    return result


def make_course(db, **kwargs):
    c = Course(
        name=kwargs.get("name", "Test course"),
        description=kwargs.get("description", "desc"),
        level=kwargs.get("level", "1"),
        language=kwargs.get("language", "ru"),
        owner_id=kwargs.get("owner_id"),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def make_cs(db, **kwargs):
    cs = CourseStructure(
        id=kwargs["course_id"],
        sections=kwargs.get("sections", 1),
        lessons_per_section=kwargs.get("lessons_per_section", 2),
        tests_per_section=kwargs.get("tests_per_section", 1),
        questions_per_test=kwargs.get("questions_per_test", 2),
        final_test=kwargs.get("final_test", True),
        content_types=json.dumps(kwargs.get("content_types", ["theory"])),
        owner_id=kwargs.get("owner_id"),
    )
    db.add(cs)
    db.commit()
    db.refresh(cs)
    return cs


def make_module(db, **kwargs):
    m = Module(course_id=kwargs["course_id"], title=kwargs.get("title", "M1"))
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def make_lesson(db, **kwargs):
    l = Lesson(module_id=kwargs["module_id"], title=kwargs.get("title", "L1"), description="d")
    db.add(l)
    db.commit()
    db.refresh(l)
    return l
