from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.assessment_rubric import AssessmentRubric
from app.models.competency import Competency
from app.models.course import Course
from app.models.learning_objective import LearningObjective
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.task import Task
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
        result.append({
            "logical_id": str(item.get("id") or f"{test_node['id']}:question:{len(result)}"),
            "question": question,
            "answers": [str(answer) for answer in answers],
            "correct": correct,
            "source_refs": list(item.get("source_refs") or test_node.get("source_refs") or []),
        })
    return result


class CourseMaterializationService:
    @staticmethod
    def materialize_learning_map(db: Session, *, course: Course, result) -> dict:
        for item in course.competencies:
            if not item.is_deleted:
                item.is_deleted = True
        for item in course.learning_objectives:
            if not item.is_deleted:
                item.is_deleted = True

        roles_by_competency: dict[str, list[str]] = {}
        for role in result.competency_map.roles:
            for competency_id in role.competency_ids:
                roles_by_competency.setdefault(competency_id, []).append(role.title)
        for competency in result.competency_map.competencies:
            db.add(
                Competency(
                    course_id=course.id,
                    title=competency.title,
                    description=competency.description,
                    level=competency.level,
                    job_role=", ".join(roles_by_competency.get(competency.id, [])) or None,
                )
            )

        modules_by_objective: dict[str, list[str]] = {}
        lessons_by_objective: dict[str, list[str]] = {}
        for module in result.course_plan.modules:
            for objective_id in module.objective_ids:
                modules_by_objective.setdefault(objective_id, []).append(module.id)
        for lesson in result.course_plan.lessons:
            for objective_id in lesson.objective_ids:
                lessons_by_objective.setdefault(objective_id, []).append(lesson.id)
        for objective in result.course_plan.objectives:
            db.add(
                LearningObjective(
                    course_id=course.id,
                    bloom_level=objective.bloom_level,
                    measurable_verb=objective.measurable_verb,
                    text=objective.text,
                    linked_node_ids=[
                        *modules_by_objective.get(objective.id, []),
                        *lessons_by_objective.get(objective.id, []),
                    ],
                )
            )
        db.flush()
        return {
            "competency_count": len(result.competency_map.competencies),
            "learning_objective_count": len(result.course_plan.objectives),
        }

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
        for rubric in course.assessment_rubrics:
            if not rubric.is_deleted:
                rubric.is_deleted = True

        materialized_modules: list[Module] = []
        persisted_ids: dict[str, list[str]] = {}
        canvas_nodes: list[dict] = []
        canvas_edges: list[dict] = []
        lesson_count = 0
        test_count = 0
        task_count = 0
        rubric_count = 0
        for module_position, module_id in enumerate(module_ids):
            node = by_id[module_id]
            module = Module(
                course_id=course.id,
                title=str(node.get("label") or module_id),
                description=str(node.get("description") or ""),
                position=module_position,
            )
            db.add(module)
            db.flush()
            materialized_modules.append(module)
            module_canvas_id = f"module:{module.id}"
            persisted_ids[module_id] = [module_canvas_id]
            canvas_nodes.append({
                "id": module_canvas_id,
                "logical_id": module_id,
                "type": "module",
                "label": module.title,
                "description": module.description,
                "position": module.position,
                "source_refs": list(node.get("source_refs") or []),
            })
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
                lesson_canvas_id = f"lesson:{lesson.id}"
                persisted_ids[lesson_id] = [lesson_canvas_id]
                content = str(lesson_node.get("content") or lesson_node.get("description") or "")
                db.add(Theory(lesson_id=lesson.id, content=content))
                canvas_nodes.append({
                    "id": lesson_canvas_id,
                    "logical_id": lesson_id,
                    "type": "lesson",
                    "label": lesson.title,
                    "description": lesson.description or "",
                    "content": content,
                    "position": lesson.position,
                    "source_refs": list(lesson_node.get("source_refs") or []),
                })
                assessment_items = [
                    ("practice", item) for item in lesson_node.get("practices", [])
                ] + [("case", item) for item in lesson_node.get("cases", [])]
                for kind, item in assessment_items:
                    if kind == "practice":
                        description = "\n\n".join(
                            value
                            for value in (
                                item.get("instructions"),
                                f"Результат: {item.get('deliverable')}"
                                if item.get("deliverable")
                                else None,
                            )
                            if value
                        )
                    else:
                        description = "\n\n".join(
                            value
                            for value in (
                                item.get("scenario"),
                                "\n".join(item.get("prompts") or []),
                                f"Ожидаемый ответ: {item.get('expected_response')}"
                                if item.get("expected_response")
                                else None,
                            )
                            if value
                        )
                    task = Task(
                        module_id=module.id,
                        name=str(item.get("title") or item.get("id") or "Задание"),
                        description=description,
                    )
                    db.add(task)
                    db.flush()
                    task_logical_id = str(item.get("id") or f"{lesson_id}:{kind}:{task.id}")
                    task_canvas_id = f"task:{task.id}"
                    persisted_ids[task_logical_id] = [task_canvas_id]
                    canvas_nodes.append({
                        "id": task_canvas_id,
                        "logical_id": task_logical_id,
                        "type": "task",
                        "label": task.name,
                        "description": task.description or "",
                        "source_refs": list(item.get("source_ref_ids") or []),
                    })
                    canvas_edges.append({
                        "source": lesson_canvas_id,
                        "target": task_canvas_id,
                        "relation": "contains",
                    })
                    task_count += 1
                    rubric = item.get("rubric")
                    if rubric:
                        levels = [
                            {"criterion_id": criterion.get("id"), **level}
                            for criterion in rubric.get("criteria", [])
                            for level in criterion.get("levels", [])
                        ]
                        db.add(
                            AssessmentRubric(
                                course_id=course.id,
                                task_id=task.id,
                                criteria=rubric.get("criteria", []),
                                levels=levels,
                            )
                        )
                        rubric_count += 1
                lesson_count += 1
            for test_node_id in module_tests[module_id]:
                for position, question in enumerate(_question_payload(by_id[test_node_id])):
                    test = Test(
                        module_id=module.id,
                        assessment_scope="module",
                        position=position,
                        question=question["question"],
                        answers=json.dumps(question["answers"], ensure_ascii=False),
                        correct_answer=question["correct"],
                    )
                    db.add(test)
                    db.flush()
                    test_canvas_id = f"test:{test.id}"
                    persisted_ids.setdefault(test_node_id, []).append(test_canvas_id)
                    persisted_ids[question["logical_id"]] = [test_canvas_id]
                    canvas_nodes.append({
                        "id": test_canvas_id,
                        "logical_id": question["logical_id"],
                        "type": "test",
                        "assessment_scope": "module",
                        "label": question["question"],
                        "question": question["question"],
                        "answers": question["answers"],
                        "correct_answer": question["correct"],
                        "position": position,
                        "source_refs": question["source_refs"],
                    })
                    test_count += 1

        final_nodes = [node for node in nodes if node.get("type") == "test" and node.get("assessment_scope") == "final"]
        for final_node in final_nodes:
            for position, question in enumerate(_question_payload(final_node)):
                test = Test(
                    course_id=course.id,
                    assessment_scope="final",
                    position=position,
                    question=question["question"],
                    answers=json.dumps(question["answers"], ensure_ascii=False),
                    correct_answer=question["correct"],
                )
                db.add(test)
                db.flush()
                test_canvas_id = f"test:{test.id}"
                logical_test_id = str(final_node["id"])
                persisted_ids.setdefault(logical_test_id, []).append(test_canvas_id)
                persisted_ids[question["logical_id"]] = [test_canvas_id]
                canvas_nodes.append({
                    "id": test_canvas_id,
                    "logical_id": question["logical_id"],
                    "type": "test",
                    "assessment_scope": "final",
                    "label": question["question"],
                    "question": question["question"],
                    "answers": question["answers"],
                    "correct_answer": question["correct"],
                    "position": position,
                    "source_refs": question["source_refs"],
                })
                test_count += 1
        for edge in edges:
            sources = persisted_ids.get(str(edge["source"]), [])
            targets = persisted_ids.get(str(edge["target"]), [])
            for source in sources:
                for target in targets:
                    canvas_edges.append({
                        "source": source,
                        "target": target,
                        "relation": edge.get("relation") or "contains",
                    })
        unique_edges = []
        seen_edges = set()
        for edge in canvas_edges:
            key = (edge["source"], edge["target"], edge["relation"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(edge)
        db.flush()
        return {
            "module_ids": [module.id for module in materialized_modules],
            "module_count": len(materialized_modules),
            "lesson_count": lesson_count,
            "test_question_count": test_count,
            "task_count": task_count,
            "rubric_count": rubric_count,
            "canvas_nodes": canvas_nodes,
            "canvas_edges": unique_edges,
        }
