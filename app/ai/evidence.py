"""Bounded evidence selection without silently dropping the ends of documents."""
import copy
import math
import re
from collections import Counter
from app.schemas.agentic_pipeline import IngestionArtifact


def terms(text: str) -> list[str]:
    # Prefix matching also handles common Russian inflections without a remote NLP model.
    return [word[:5] if re.search("[а-яё]", word) and len(word) > 4 else word[:6]
            for word in re.findall(r"[\w]+", text.lower()) if len(word) > 2]


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



def ingestion_sources(catalog: list[dict]) -> list[dict]:
    """Only useful evidence enters the prompt; canonical provenance stays server-side."""
    fields = {"id", "document_id", "document_version", "quote", "section", "page",
              "source_type", "source_url", "source_title", "retrieved_at", "evidence_kind"}
    return [{key: value for key, value in source.items() if key in fields and value is not None}
            for source in catalog]


def complete_ingestion_inventories(raw: dict, catalog: list[dict]) -> dict:
    """Derive redundant indexes, never invent, repair or discard semantic claims."""
    from app.services.agent_runtime import expand_source_refs
    if not isinstance(raw, dict):
        raise ValueError("Ingestion must be an object")
    canonical = {source["id"]: source for source in catalog}
    # Even optional inventories may not introduce unknown or changed evidence.
    expand_source_refs({"source_refs": raw.get("source_refs", [])}, canonical)
    grouped = {}
    for source in catalog:
        grouped.setdefault((source["document_id"], source["document_version"]), []).append(source)
    summaries = {}
    for doc in raw.get("documents", []):
        key = (doc.get("document_id"), doc.get("document_version"))
        if key not in grouped or key in summaries:
            raise ValueError("Unknown or duplicate document version")
        if set(doc.get("source_ref_ids", [])) - {s["id"] for s in grouped[key]}:
            raise ValueError("Unknown document citation")
        summaries[key] = doc
    items = raw.get("knowledge_items", [])
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("source_ref_ids"), list):
            raise ValueError("Knowledge items require source citations")
        if any(not isinstance(ref, str) or ref not in canonical for ref in item["source_ref_ids"]):
            raise ValueError("Unknown knowledge citation")
    documents = []
    for (doc_id, version), sources in grouped.items():
        refs = {s["id"] for s in sources}
        linked = [item for item in items if refs.intersection(item["source_ref_ids"])]
        prior = summaries.get((doc_id, version), {})
        documents.append({"document_id": doc_id, "document_version": version,
            "title": prior.get("title") or sources[0].get("source_title") or f"Документ {doc_id}",
            "summary": prior.get("summary") or ("; ".join(str(item.get("title", "")) for item in linked)[:8000]
                if linked else "В этом фрагменте не выделены утверждения для обучения."),
            "source_ref_ids": [s["id"] for s in sources],
            "knowledge_item_ids": [item.get("id") for item in linked]})
    return {**raw, "source_refs": list(canonical), "documents": documents}

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
