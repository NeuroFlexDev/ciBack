"""Bounded evidence selection without silently dropping the ends of documents."""
import copy
import math
import re
from collections import Counter
from app.schemas.agentic_pipeline import IngestionArtifact


def terms(text: str) -> list[str]:
    # Prefix matching also handles common Russian inflections without a remote NLP model.
    return [word[:6] if len(word) > 6 else word for word in re.findall(r"[\w]+", text.lower()) if len(word) > 2]


def rank_sources(query: str, sources: list[dict], limit: int = 6) -> list[dict]:
    query_terms = set(terms(query))
    bags = [Counter(terms(item.get("quote", ""))) for item in sources]
    df = Counter(term for bag in bags for term in bag)
    average = sum(sum(bag.values()) for bag in bags) / max(1, len(bags))
    scored = []
    for index, bag in enumerate(bags):
        length = sum(bag.values())
        score = 0.0
        for term in query_terms:
            frequency = bag.get(term, 0)
            if frequency:
                idf = math.log(1 + (len(bags) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * length / max(average, 1)))
        scored.append((score, index))
    return [sources[index] for score, index in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit] if score > 0]


def source_batches(catalog: list[dict], max_chars: int) -> list[list[dict]]:
    batches, current, size = [], [], 0
    for source in catalog:
        length = len(source["quote"])
        if current and size + length > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(source)
        size += length
    if current:
        batches.append(current)
    return batches


def merge_ingestion(artifacts: list[IngestionArtifact]) -> IngestionArtifact:
    if len(artifacts) == 1:
        return artifacts[0]
    sources, documents, knowledge, warnings = {}, {}, [], []
    for index, artifact in enumerate(artifacts):
        payload = copy.deepcopy(artifact.model_dump(mode="json"))
        names = {item["id"]: f"kn:b{index}:" + item["id"][3:110] for item in payload["knowledge_items"]}
        for item in payload["knowledge_items"]:
            item["id"] = names[item["id"]]
            item["related_item_ids"] = [names[ref] for ref in item["related_item_ids"]]
            knowledge.append(item)
        sources.update({item["id"]: item for item in payload["source_refs"]})
        for document in payload["documents"]:
            key = (document["document_id"], document["document_version"])
            document["knowledge_item_ids"] = [names[ref] for ref in document["knowledge_item_ids"]]
            if key not in documents:
                documents[key] = document
            else:
                existing = documents[key]
                existing["source_ref_ids"] = sorted(set(existing["source_ref_ids"] + document["source_ref_ids"]))
                existing["knowledge_item_ids"].extend(document["knowledge_item_ids"])
                existing["summary"] = (existing["summary"] + "\n" + document["summary"])[:8000]
        warnings.extend(payload["warnings"])
    return IngestionArtifact(source_refs=list(sources.values()), documents=list(documents.values()),
                             knowledge_items=knowledge, warnings=warnings)


def compact_artifact(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, list):
        return [compact_artifact(item) for item in value]
    if isinstance(value, dict):
        return {key: ([item["id"] if isinstance(item, dict) else item for item in child]
                      if key == "source_refs" and isinstance(child, list) else compact_artifact(child))
                for key, child in value.items()}
    return value


def learner_markdown(text: str, source_ids: set[str]) -> str:
    """Hide internal citation markers; canonical citation metadata is retained."""
    def replace(match):
        if match[1] not in source_ids:
            raise ValueError(f"Inline citation is not declared in section evidence: {match[1]}")
        return ""
    return re.sub(r"\[(src:[^\]\s]+)\]", replace, text).strip()
