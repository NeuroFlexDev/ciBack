import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, meta

from app.schemas.canvas_generation import CanvasGenerationPayload
from app.schemas.pipeline import GeneratedGraphPayload


PROMPTS = Path(__file__).resolve().parents[1] / "app" / "prompts"
ENV = Environment(
    loader=FileSystemLoader(PROMPTS),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _render(name: str, **context) -> str:
    return ENV.get_template(name).render(**context)


def test_document_to_competencies_is_thin_canonical_alias():
    context = {
        "target_role": "operator",
        "target_level": "basic",
        "language": "ru",
        "ingestion_artifact_json": '{"artifact_version":"1.0"}',
    }

    public = _render("document_to_competencies_prompt.j2", **context)
    canonical = _render("competency_mapper_prompt.j2", **context)

    assert public.strip() == canonical.strip()
    assert "CompetencyMapArtifact" in public
    assert context["ingestion_artifact_json"] in public


def test_competencies_to_course_graph_is_thin_canonical_alias():
    context = {
        "course_title": "Safe operations",
        "goal": "Apply the operating procedure",
        "target_audience": "operators",
        "difficulty": "basic",
        "language": "ru",
        "lesson_count": 1,
        "available_minutes": 60,
        "competency_map_json": '{"artifact_version":"1.0"}',
        "source_catalog_json": "[]",
    }

    public = _render("competencies_to_course_graph_prompt.j2", **context)
    canonical = _render("course_architect_prompt.j2", **context)

    assert public.strip() == canonical.strip()
    assert "CoursePlan" in public
    assert "Create exactly 1 lessons" in public
    assert context["competency_map_json"] in public


def test_course_graph_to_canvas_declares_stable_inputs_and_json_contract():
    template_name = "course_graph_to_canvas_prompt.j2"
    template_source = (PROMPTS / template_name).read_text(encoding="utf-8")
    parsed = ENV.parse(template_source)

    assert meta.find_undeclared_variables(parsed) == {
        "assessment_settings_json",
        "available_minutes",
        "course_plan_json",
        "difficulty",
        "final_test_enabled",
        "language",
        "module_tests_enabled",
        "qa_feedback_json",
        "source_catalog_json",
    }

    rendered = _render(
        template_name,
        language="ru",
        difficulty="basic",
        module_tests_enabled=True,
        final_test_enabled=False,
        available_minutes=60,
        qa_feedback_json="null",
        assessment_settings_json='{"module_tests_enabled":true}',
        course_plan_json='{"id":"plan:example_course"}',
        source_catalog_json='[{"id":"src:doc:1:v1:chunk:1"}]',
    )

    assert "CanvasGenerationPayload" in rendered
    assert 'Use `schema_version` exactly "1.0"' in rendered
    assert '"artifact_version"' not in rendered
    assert '<UNTRUSTED_COURSE_PLAN>\n{"id":"plan:example_course"}' in rendered
    assert '<UNTRUSTED_SOURCE_CATALOG>\n[{"id":"src:doc:1:v1:chunk:1"}]' in rendered

    example = rendered.split(
        "Required JSON shape (all keys shown are contract keys):", 1
    )[1].split("Authoritative application settings:", 1)[0]
    payload = json.loads(example)

    assert payload["schema_version"] == "1.0"
    assert set(payload) == {
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
    assert {item["kind"] for item in payload["assignments"]} == {
        "practice",
        "case",
    }
    assert payload["tests"][0]["scope"] == "module"

    contract = CanvasGenerationPayload.model_validate(payload)
    nodes, edges = contract.json_payload()
    GeneratedGraphPayload.model_validate({"nodes": nodes, "edges": edges})

    assert {item["type"] for item in nodes} == {"module", "lesson", "test"}
    assert any(item.get("practices") for item in nodes if item["type"] == "lesson")
    assert any(item.get("cases") for item in nodes if item["type"] == "lesson")
