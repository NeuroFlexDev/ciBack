"""Bounded curriculum slices; IDs and evidence retain their original meaning."""
from app.schemas.agentic_pipeline import CoursePlan, WriterArtifact, AssessmentArtifact


def complete_competency_inventories(raw: dict, *, sources: set[str], knowledge: set[str]) -> dict:
    """Complete redundant citation indexes, never invent ontology nodes or evidence.

    A model may cite a valid source in a leaf but omit it from the top-level
    inventory. That inventory is bookkeeping that the server can derive exactly.
    Unknown IDs and malformed node references remain hard validation errors.
    """
    import copy
    result = copy.deepcopy(raw)
    if not isinstance(result, dict):
        return result
    for field, allowed, arrays in (
        ("source_refs", sources, ("roles", "competencies", "skills", "knowledge", "procedures")),
        ("source_knowledge_item_ids", knowledge, ("knowledge", "procedures")),
    ):
        inventory = result.get(field)
        if not isinstance(inventory, list):
            continue
        indexed = set()
        for item in inventory:
            ref = item.get("id") if isinstance(item, dict) else item
            if not isinstance(ref, str) or ref not in allowed:
                raise ValueError(f"Unknown {field} ID: {ref}")
            indexed.add(ref)
        citation_field = "source_ref_ids" if field == "source_refs" else field
        for array in arrays:
            nodes = result.get(array, [])
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                refs = node.get(citation_field, []) if isinstance(node, dict) else []
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if not isinstance(ref, str) or ref not in allowed:
                        raise ValueError(f"Unknown {citation_field} ID: {ref}")
                    if ref not in indexed:
                        inventory.append(ref)
                        indexed.add(ref)
    return result


def slice_curriculum(plan: CoursePlan, writer: WriterArtifact, lesson_ids: set[str]):
    lessons = [lesson.model_copy(update={"prerequisite_lesson_ids": [i for i in lesson.prerequisite_lesson_ids if i in lesson_ids]})
               for lesson in plan.lessons if lesson.id in lesson_ids]
    objectives = {i for lesson in lessons for i in lesson.objective_ids}
    competencies = {i for lesson in lessons for i in lesson.competency_ids}
    modules = []
    for module in plan.modules:
        members = [lesson for lesson in lessons if lesson.id in module.lesson_ids]
        if members:
            modules.append(module.model_copy(update={
                "lesson_ids": [lesson.id for lesson in members],
                "objective_ids": sorted({i for lesson in members for i in lesson.objective_ids}),
                "competency_ids": sorted({i for lesson in members for i in lesson.competency_ids}),
                "prerequisite_module_ids": [],
                "source_ref_ids": sorted({i for lesson in members for i in lesson.source_ref_ids}
                    | {ref for objective in plan.objectives
                       if objective.id in {i for lesson in members for i in lesson.objective_ids}
                       for ref in objective.source_ref_ids}),
            }))
    draft_lessons = [lesson for lesson in writer.lessons if lesson.id in lesson_ids]
    refs = {i for lesson in [*lessons, *draft_lessons] for i in lesson.source_ref_ids}
    refs.update(i for module in modules for i in module.source_ref_ids)
    refs.update(i for objective in plan.objectives if objective.id in objectives for i in objective.source_ref_ids)
    sources = {ref.id: ref for ref in [*plan.source_refs, *writer.source_refs] if ref.id in refs}
    partial_plan = plan.model_copy(update={"lessons": lessons, "modules": modules,
        "objectives": [o for o in plan.objectives if o.id in objectives],
        "competency_ids": sorted(competencies), "source_refs": list(sources.values()),
        "estimated_minutes": sum(lesson.estimated_minutes for lesson in lessons)})
    partial_writer = writer.model_copy(update={"lessons": draft_lessons, "expected_lesson_ids": sorted(lesson_ids),
        "objective_ids": sorted(objectives), "competency_ids": sorted(competencies), "source_refs": list(sources.values())})
    return partial_plan, partial_writer


def merge_assessments(parts: list[AssessmentArtifact]) -> AssessmentArtifact:
    """Namespace generated IDs to avoid collisions between independent agents."""
    import hashlib
    payload = parts[0].model_dump(mode="json")
    for field in ("questions", "practices", "cases", "rubrics"):
        payload[field] = []
    for index, part in enumerate(parts):
        def visit(value):
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {key: visit(item) for key, item in value.items()}
            if isinstance(value, str) and value.partition(":")[0] in {
                "question", "option", "practice", "case", "rubric", "criterion", "level"
            } and " " not in value:
                prefix, _, suffix = value.partition(":")
                return f"{prefix}:b{index}:" + suffix[:85] + ":" + hashlib.sha256(value.encode()).hexdigest()[:8]
            return value
        data = visit(part.model_dump(mode="json"))
        for field in ("questions", "practices", "cases", "rubrics"):
            payload[field].extend(data[field])
    for field in ("module_ids", "lesson_ids", "objective_ids", "competency_ids"):
        payload[field] = sorted({item for part in parts for item in getattr(part, field)})
    payload["source_refs"] = list({ref.id: ref.model_dump(mode="json") for part in parts for ref in part.source_refs}.values())
    return AssessmentArtifact.model_validate(payload)


def assessment_excerpt(assessment, plan):
    """Keep only assessments aligned with this review batch."""
    objectives = {o.id for o in plan.objectives}
    lessons = {lesson.id for lesson in plan.lessons}
    modules = {module.id for module in plan.modules}
    data = assessment.model_dump(mode="json")
    for field in ("questions", "practices", "cases"):
        data[field] = [item for item in data[field] if objectives.intersection(item["objective_ids"])]
    data["questions"] = [q for q in data["questions"] if q["scope"] == "final" or
                         q["target_id"] in (lessons if q["scope"] == "lesson" else modules)]
    data["practices"] = [item for item in data["practices"] if item["lesson_id"] in lessons]
    data["cases"] = [item for item in data["cases"] if lessons.intersection(item["lesson_ids"])]
    rubric_ids = {item["rubric_id"] for field in ("practices", "cases") for item in data[field]}
    data["rubrics"] = [item for item in data["rubrics"] if item["id"] in rubric_ids]
    data["module_ids"] = [m.id for m in plan.modules]
    data["lesson_ids"] = [l.id for l in plan.lessons]
    data["objective_ids"] = sorted(objectives)
    data["competency_ids"] = plan.competency_ids
    refs = {ref for field in ("questions", "practices", "cases", "rubrics") for item in data[field] for ref in item["source_ref_ids"]}
    data["source_refs"] = [ref for ref in data["source_refs"] if ref["id"] in refs]
    return data


def competency_excerpt(competency_map, plan):
    data = competency_map.model_dump(mode="json")
    data["competencies"] = [item for item in data["competencies"] if item["id"] in plan.competency_ids]
    skills = {i for item in data["competencies"] for i in item["skill_ids"]}
    data["skills"] = [item for item in data["skills"] if item["id"] in skills]
    knowledge = {i for item in data["skills"] for i in item["knowledge_ids"]}
    procedures = {i for item in data["skills"] for i in item["procedure_ids"]}
    data["knowledge"] = [item for item in data["knowledge"] if item["id"] in knowledge]
    data["procedures"] = [item for item in data["procedures"] if item["id"] in procedures]
    data["roles"] = [{**item, "competency_ids": [i for i in item["competency_ids"] if i in plan.competency_ids]}
                     for item in data["roles"] if set(item["competency_ids"]).intersection(plan.competency_ids)]
    data["source_knowledge_item_ids"] = sorted({i for item in [*data["knowledge"], *data["procedures"]] for i in item["source_knowledge_item_ids"]})
    refs = {i for field in ("roles", "competencies", "skills", "knowledge", "procedures") for item in data[field] for i in item["source_ref_ids"]}
    data["source_refs"] = [item for item in data["source_refs"] if item["id"] in refs]
    return data
