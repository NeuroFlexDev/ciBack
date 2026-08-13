from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.test import Test
from app.models.theory import Theory


def _ordered_ids(nodes: list[dict], edges: list[dict], node_type: str) -> list[str]:
    candidates = [str(node["id"]) for node in nodes if node.get("type") == node_type]
    candidate_set = set(candidates)
    follows: dict[str, set[str]] = {item: set() for item in candidates}
    indegree = {item: 0 for item in candidates}
    for edge in edges:
        source, target = str(edge["source"]), str(edge["target"])
        if edge.get("relation") != "precedes" or source not in candidate_set or target not in candidate_set:
            continue
        if target not in follows[source]:
            follows[source].add(target)
            indegree[target] += 1
    source_order = {item: index for index, item in enumerate(candidates)}
    ready = sorted((item for item in candidates if indegree[item] == 0), key=source_order.get)
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for target in sorted(follows[current], key=source_order.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=source_order.get)
    if len(result) != len(candidates):
        raise ValueError(f"generated {node_type} ordering contains a cycle")
    return result


def _question_payload(test_node: dict) -> list[dict]:
    questions = test_node.get("questions") or []
    if not questions:
        questions = [{
            "question": test_node.get("label") or "Контрольный вопрос",
            "answers": [],
            "correct_answer": "",
        }]
    result = []
    for item in questions:
        question = str(item.get("question") or "").strip()
        answers = item.get("answers") or []
        correct = str(item.get("correct_answer", item.get("correct", ""))).strip()
        if not question or not isinstance(answers, list):
            raise ValueError("generated test question is invalid")
        result.append({"question": question, "answers": [str(answer) for answer in answers], "correct": correct})
    return result


class CourseMaterializationService:
    @staticmethod
    def materialize(
        db: Session, *, course: Course, nodes: list[dict], edges: list[dict]
    ) -> dict:
        by_id = {str(node["id"]): node for node in nodes}
        module_ids = _ordered_ids(nodes, edges, "module")
        lesson_ids = set(_ordered_ids(nodes, edges, "lesson"))
        contains: dict[str, list[str]] = {module_id: [] for module_id in module_ids}
        module_tests: dict[str, list[str]] = {module_id: [] for module_id in module_ids}
        for edge in edges:
            source, target = str(edge["source"]), str(edge["target"])
            target_node = by_id.get(target, {})
            relation = edge.get("relation")
            if relation not in {None, "contains"}:
                continue
            if source in contains and target_node.get("type") == "lesson":
                contains[source].append(target)
            elif source in module_tests and target_node.get("type") == "test":
                module_tests[source].append(target)
        assigned_lessons = {item for items in contains.values() for item in items}
        if assigned_lessons != lesson_ids:
            raise ValueError("every generated lesson must belong to exactly one module")
        if sum(len(items) for items in contains.values()) != len(assigned_lessons):
            raise ValueError("generated lesson belongs to more than one module")

        for old_module in course.modules:
            if old_module.is_deleted:
                continue
            old_module.is_deleted = True
            for lesson in old_module.lessons:
                lesson.is_deleted = True
                if lesson.theory is not None:
                    lesson.theory.is_deleted = True
            for test in old_module.tests:
                test.is_deleted = True
            for task in old_module.tasks:
                task.is_deleted = True
        for test in course.final_tests:
            if not test.is_deleted:
                test.is_deleted = True

        materialized_modules: list[Module] = []
        lesson_count = 0
        test_count = 0
        for module_position, module_id in enumerate(module_ids):
            node = by_id[module_id]
            module = Module(
                course_id=course.id,
                title=str(node.get("label") or module_id),
                position=module_position,
            )
            db.add(module)
            db.flush()
            materialized_modules.append(module)
            ordered_lessons = [item for item in _ordered_ids(nodes, edges, "lesson") if item in contains[module_id]]
            for lesson_position, lesson_id in enumerate(ordered_lessons):
                lesson_node = by_id[lesson_id]
                lesson = Lesson(
                    module_id=module.id,
                    title=str(lesson_node.get("label") or lesson_id),
                    description=lesson_node.get("description"),
                    position=lesson_position,
                )
                db.add(lesson)
                db.flush()
                content = str(lesson_node.get("content") or lesson_node.get("description") or "")
                db.add(Theory(lesson_id=lesson.id, content=content))
                lesson_count += 1
            for test_node_id in module_tests[module_id]:
                for position, question in enumerate(_question_payload(by_id[test_node_id])):
                    db.add(Test(
                        module_id=module.id,
                        assessment_scope="module",
                        position=position,
                        question=question["question"],
                        answers=json.dumps(question["answers"], ensure_ascii=False),
                        correct_answer=question["correct"],
                    ))
                    test_count += 1

        final_nodes = [node for node in nodes if node.get("type") == "test" and node.get("assessment_scope") == "final"]
        for final_node in final_nodes:
            for position, question in enumerate(_question_payload(final_node)):
                db.add(Test(
                    course_id=course.id,
                    assessment_scope="final",
                    position=position,
                    question=question["question"],
                    answers=json.dumps(question["answers"], ensure_ascii=False),
                    correct_answer=question["correct"],
                ))
                test_count += 1
        db.flush()
        return {
            "module_ids": [module.id for module in materialized_modules],
            "module_count": len(materialized_modules),
            "lesson_count": lesson_count,
            "test_question_count": test_count,
        }
