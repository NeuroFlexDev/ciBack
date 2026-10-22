"""Bound whole evidence records; preserve the complete durable ontology."""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from app.ai.context_budget import prompt_fits, require_fits
from app.ai.models import model_policy
from app.schemas.agentic_pipeline import CompetencyMapArtifact


def partition_records(records: list, context: Callable[[list], dict], *, role: str, template: str) -> list[list]:
    """Stable batches by the complete rendered prompt, not quote character count.

    No record is clipped, discarded or summarized. An indivisible oversized
    record fails explicitly before dispatch rather than silently losing facts.
    """
    if not records:
        return []
    batches, offset = [], 0
    while offset < len(records):
        low, high, size = 1, len(records) - offset, 0
        while low <= high:
            middle = (low + high) // 2
            if prompt_fits(role, template, context(records[offset:offset + middle])):
                size, low = middle, middle + 1
            else:
                high = middle - 1
        if not size:
            from app.services.generation_service import agent_messages, render_prompt
            messages = agent_messages(render_prompt(template, **context(records[offset:offset + 1])), expect_json=True)
            policy = model_policy(role)
            # If the record fits without a repair reserve, allow a single call;
            # any repair will still be checked independently by AgentRuntime.
            require_fits(messages, policy.model, policy.max_output_tokens)
            size = 1
        batches.append(records[offset:offset + size])
        offset += size
    assert sum(map(len, batches)) == len(records)
    return batches


def ingestion_excerpt(ingestion: dict, records: list[dict]) -> dict:
    ids = {item["id"] for item in records}
    refs = {ref for item in records for ref in item["source_ref_ids"]}
    documents = []
    for document in ingestion["documents"]:
        selected = [item for item in document["knowledge_item_ids"] if item in ids]
        if selected:
            documents.append({**document, "knowledge_item_ids": selected,
                              "source_ref_ids": [ref for ref in document["source_ref_ids"] if ref in refs]})
    return {**ingestion, "knowledge_items": records, "documents": documents,
            "source_refs": [item for item in ingestion["source_refs"] if item["id"] in refs]}


def merge_competencies(parts: list[CompetencyMapArtifact]) -> CompetencyMapArtifact:
    if len(parts) == 1:
        return parts[0]
    fields = ("roles", "competencies", "skills", "knowledge", "procedures")
    result = {"artifact_version": "1.0", **{field: [] for field in fields}}
    for index, part in enumerate(parts):
        data = part.model_dump(mode="json")
        names = {item["id"]: item["id"].partition(":")[0] + f":b{index}:" +
                 hashlib.sha256(item["id"].encode()).hexdigest()[:24]
                 for field in fields for item in data[field]}
        def rename(value):
            if isinstance(value, list):
                return [rename(item) for item in value]
            if isinstance(value, dict):
                return {key: rename(item) for key, item in value.items()}
            return names.get(value, value) if isinstance(value, str) else value
        for field in fields:
            result[field].extend(rename(copy.deepcopy(data[field])))
    result["source_refs"] = list({ref.id: ref.model_dump(mode="json") for part in parts for ref in part.source_refs}.values())
    result["source_knowledge_item_ids"] = sorted({item for part in parts for item in part.source_knowledge_item_ids})
    return CompetencyMapArtifact.model_validate(result)


def architecture_projection(ontology: dict) -> dict:
    """Planning needs all competency identities/citations, not duplicate leaf text.

    Full knowledge, procedure steps and original facts remain in the durable
    competency map and are supplied to writers/reviewers through their slices.
    """
    return {"scope": "Complete competency inventory for planning. Detailed skill/knowledge/procedure records are retained separately for lesson writing and review.",
            "roles": [{key: item[key] for key in ("id", "title", "competency_ids", "source_ref_ids")} for item in ontology["roles"]],
            "competencies": ontology["competencies"],
            "source_refs": ontology["source_refs"]}
