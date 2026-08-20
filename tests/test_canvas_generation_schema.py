from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.canvas_generation import (
    CanvasCaseAssignment,
    CanvasGenerationPayload,
    CanvasLesson,
    CanvasModule,
    CanvasPracticeAssignment,
    CanvasTest,
    CanvasTestQuestion,
)
from app.schemas.pipeline import GeneratedGraphPayload


def _rubric(rubric_id: str, criterion_id: str) -> dict:
    return {
        "id": rubric_id,
        "title": "Application rubric",
        "objective_ids": ["obj:apply"],
        "competency_ids": ["cmp:safety"],
        "source_ref_ids": ["src:policy"],
        "criteria": [
            {
                "id": criterion_id,
                "title": "Correctness",
                "description": "The procedure is applied correctly.",
                "weight": 1.0,
                "levels": [
                    {
                        "id": "level:not_met",
                        "title": "Not met",
                        "description": "The procedure is incorrect.",
                        "score": 0,
                    },
                    {
                        "id": "level:met",
                        "title": "Met",
                        "description": "The procedure is correct.",
                        "score": 1,
                    },
                ],
            }
        ],
        "passing_score": 1,
    }


def _question(question_id: str, option_prefix: str) -> dict:
    return {
        "id": question_id,
        "kind": "single_choice",
        "prompt": "What must the operator do?",
        "options": [
            {"id": f"option:{option_prefix}_check", "text": "Run the check"},
            {"id": f"option:{option_prefix}_skip", "text": "Skip the check"},
        ],
        "correct_option_ids": [f"option:{option_prefix}_check"],
        "expected_answer": None,
        "explanation": "The policy requires the check.",
        "objective_ids": ["obj:apply"],
        "competency_ids": ["cmp:safety"],
        "source_ref_ids": ["src:policy"],
    }


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "course_plan_id": "plan:safety",
        "title": "Safety course",
        "description": "A grounded operator safety course.",
        "language": "en",
        "difficulty": "basic",
        "estimated_minutes": 30,
        "objective_ids": ["obj:apply"],
        "competency_ids": ["cmp:safety"],
        "source_ref_ids": ["src:policy"],
        "modules": [
            {
                "id": "mod:safety",
                "title": "Safety",
                "description": "Required safety procedures.",
                "position": 0,
                "estimated_minutes": 30,
                "objective_ids": ["obj:apply"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": ["src:policy"],
                "prerequisite_module_ids": [],
            }
        ],
        "lessons": [
            {
                "id": "lesson:safety_check",
                "module_id": "mod:safety",
                "title": "Run the safety check",
                "description": "Apply the required safety check.",
                "content_markdown": "## Procedure\n\nRun the safety check.",
                "position": 0,
                "estimated_minutes": 30,
                "objective_ids": ["obj:apply"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": ["src:policy"],
                "prerequisite_lesson_ids": [],
            }
        ],
        "tests": [
            {
                "id": "test:module:safety",
                "title": "Module test",
                "description": "Checks the safety procedure.",
                "scope": "module",
                "module_id": "mod:safety",
                "position": 0,
                "objective_ids": ["obj:apply"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": ["src:policy"],
                "questions": [_question("question:module_check", "module")],
            },
            {
                "id": "test:final:safety",
                "title": "Final test",
                "description": "Checks the course objective.",
                "scope": "final",
                "module_id": None,
                "position": 0,
                "objective_ids": ["obj:apply"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": ["src:policy"],
                "questions": [_question("question:final_check", "final")],
            },
        ],
        "assignments": [
            {
                "kind": "practice",
                "id": "practice:safety_check",
                "lesson_id": "lesson:safety_check",
                "position": 0,
                "title": "Perform the check",
                "instructions": "Run the documented procedure.",
                "deliverable": "Completed checklist",
                "objective_ids": ["obj:apply"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": ["src:policy"],
                "rubric": _rubric(
                    "rubric:practice_check", "criterion:practice_correct"
                ),
            },
            {
                "kind": "case",
                "id": "case:skipped_check",
                "lesson_id": "lesson:safety_check",
                "position": 1,
                "title": "Skipped check",
                "scenario": "An operator skipped the required check.",
                "prompts": ["What should happen next?"],
                "expected_response": "Stop and perform the check.",
                "objective_ids": ["obj:apply"],
                "competency_ids": ["cmp:safety"],
                "source_ref_ids": ["src:policy"],
                "rubric": _rubric("rubric:case_check", "criterion:case_correct"),
            },
        ],
    }


def test_contract_declares_exact_fields_for_each_canvas_entity():
    assert set(CanvasModule.model_fields) == {
        "id",
        "title",
        "description",
        "position",
        "estimated_minutes",
        "objective_ids",
        "competency_ids",
        "source_ref_ids",
        "prerequisite_module_ids",
    }
    assert set(CanvasLesson.model_fields) == {
        "id",
        "module_id",
        "title",
        "description",
        "content_markdown",
        "position",
        "estimated_minutes",
        "objective_ids",
        "competency_ids",
        "source_ref_ids",
        "prerequisite_lesson_ids",
    }
    assert set(CanvasTest.model_fields) == {
        "id",
        "title",
        "description",
        "scope",
        "module_id",
        "position",
        "objective_ids",
        "competency_ids",
        "source_ref_ids",
        "questions",
    }
    common_assignment_fields = {
        "id",
        "kind",
        "lesson_id",
        "position",
        "title",
        "objective_ids",
        "competency_ids",
        "source_ref_ids",
        "rubric",
    }
    assert set(CanvasPracticeAssignment.model_fields) == common_assignment_fields | {
        "instructions",
        "deliverable",
    }
    assert set(CanvasCaseAssignment.model_fields) == common_assignment_fields | {
        "scenario",
        "prompts",
        "expected_response",
    }
    assert set(CanvasTestQuestion.model_fields) == {
        "id",
        "kind",
        "prompt",
        "options",
        "correct_option_ids",
        "expected_answer",
        "explanation",
        "objective_ids",
        "competency_ids",
        "source_ref_ids",
    }


def test_canvas_payload_validates_and_converts_to_current_graph_contract():
    payload = CanvasGenerationPayload.model_validate(valid_payload())

    nodes, edges = payload.json_payload()
    GeneratedGraphPayload.model_validate({"nodes": nodes, "edges": edges})

    assert [node["type"] for node in nodes] == [
        "module",
        "lesson",
        "test",
        "test",
    ]
    lesson = next(node for node in nodes if node["type"] == "lesson")
    assert lesson["content"] == "## Procedure\n\nRun the safety check."
    assert lesson["practices"][0]["rubric_id"] == "rubric:practice_check"
    assert lesson["cases"][0]["lesson_ids"] == ["lesson:safety_check"]
    assert not any(node["type"] == "task" for node in nodes)
    assert {
        (edge["source"], edge["target"], edge["relation"]) for edge in edges
    } == {
        ("mod:safety", "lesson:safety_check", "contains"),
        ("mod:safety", "test:module:safety", "contains"),
        ("mod:safety", "test:final:safety", "precedes"),
    }


def test_json_schema_is_closed_and_assignment_union_is_discriminated():
    schema = CanvasGenerationPayload.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "course_plan_id",
        "title",
        "description",
        "language",
        "difficulty",
        "estimated_minutes",
        "objective_ids",
        "competency_ids",
        "source_ref_ids",
        "modules",
        "lessons",
        "tests",
        "assignments",
    }
    assignment_items = schema["properties"]["assignments"]["items"]
    assert assignment_items["discriminator"]["propertyName"] == "kind"
    assert set(assignment_items["discriminator"]["mapping"]) == {"practice", "case"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["lessons"][0].update(module_id="mod:missing"),
        lambda value: value["tests"][1].update(module_id="mod:safety"),
        lambda value: value["assignments"][0].update(position=2),
        lambda value: value["modules"][0].update(estimated_minutes=31),
        lambda value: value["lessons"][0].update(
            prerequisite_lesson_ids=["lesson:safety_check"]
        ),
        lambda value: value["tests"][0]["questions"][0].update(
            correct_option_ids=[]
        ),
        lambda value: value["modules"][0].update(unknown_field=True),
    ],
)
def test_contract_rejects_invalid_ownership_scope_ordering_and_shape(mutate):
    data = deepcopy(valid_payload())
    mutate(data)

    with pytest.raises(ValidationError):
        CanvasGenerationPayload.model_validate(data)


def test_contract_rejects_unknown_lineage_ids():
    data = valid_payload()
    data["lessons"][0]["source_ref_ids"] = ["src:invented"]

    with pytest.raises(ValidationError, match="unknown ids"):
        CanvasGenerationPayload.model_validate(data)
