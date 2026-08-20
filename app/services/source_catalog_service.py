from __future__ import annotations

import hashlib
from collections import deque
from typing import Any, Iterable


def source_ref_id(document_id: int, document_version: int, chunk_id: int) -> str:
    return f"src:doc:{document_id}:v{document_version}:chunk:{chunk_id}"


def build_source_catalog(documents: Iterable[Any], *, max_chars: int) -> list[dict]:
    """Build a fair, immutable evidence catalog for agent prompts.

    Chunks are selected round-robin across documents instead of taking the
    first N characters from the first document. The catalog identifier is the
    only citation identifier agents are allowed to emit.
    """

    queues: list[deque[tuple[Any, Any]]] = []
    for document in sorted(documents, key=lambda item: (item.id, item.version)):
        chunks = sorted(document.chunks, key=lambda item: item.chunk_index)
        if chunks:
            queues.append(deque((document, chunk) for chunk in chunks))

    result: list[dict] = []
    remaining = max_chars
    while queues and remaining > 0:
        next_round: list[deque[tuple[Any, Any]]] = []
        for queue in queues:
            if not queue or remaining <= 0:
                continue
            document, chunk = queue.popleft()
            text = str(chunk.text or "").strip()
            if not text:
                if queue:
                    next_round.append(queue)
                continue

            included = text[:remaining]
            if not included:
                break
            result.append(
                {
                    "id": source_ref_id(document.id, document.version, chunk.id),
                    "document_id": document.id,
                    "document_version": document.version,
                    "document_content_hash": document.content_hash,
                    "chunk_id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "page": chunk.page,
                    "section": chunk.section,
                    "quote": included,
                }
            )
            remaining -= len(included)
            if queue:
                next_round.append(queue)
        queues = next_round
    return result


def normalize_ref_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("id") or value.get("ref_id")
        return str(candidate) if candidate else None
    candidate = getattr(value, "ref_id", None)
    return str(candidate) if candidate else None


def validate_source_refs(payload: Any, allowed_ref_ids: set[str]) -> set[str]:
    """Reject invented citations anywhere in an agent artifact."""

    used: set[str] = set()

    def visit(value: Any, key: str | None = None) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key in {"source_refs", "citations", "evidence"}:
                    if not isinstance(child, list):
                        raise ValueError(f"{child_key} must be a list")
                    for raw_ref in child:
                        ref_id = normalize_ref_id(raw_ref)
                        if ref_id is None or ref_id not in allowed_ref_ids:
                            raise ValueError(f"unknown source reference: {ref_id}")
                        used.add(ref_id)
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)

    visit(payload)
    return used


def graph_source_links(
    nodes: list[dict], source_catalog: list[dict]
) -> list[dict]:
    by_ref = {item["id"]: item for item in source_catalog}
    links: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        node_id = str(node["id"])
        refs = node.get("source_refs") or node.get("citations") or []
        for raw_ref in refs:
            ref_id = normalize_ref_id(raw_ref)
            if ref_id is None or ref_id not in by_ref:
                raise ValueError(f"unknown graph source reference: {ref_id}")
            key = (node_id, ref_id)
            if key in seen:
                continue
            seen.add(key)
            source = by_ref[ref_id]
            links.append(
                {
                    "node_id": node_id,
                    "target_type": str(node.get("type") or "course_node"),
                    "document_id": source["document_id"],
                    "document_version": source["document_version"],
                    "chunk_id": source["chunk_id"],
                    "chunk_index": source["chunk_index"],
                    "page": source.get("page"),
                    "section": source.get("section"),
                    "excerpt": source["quote"],
                    "excerpt_hash": hashlib.sha256(source["quote"].encode()).hexdigest(),
                    "ref_id": ref_id,
                    "relation": "supports",
                    "confidence": None,
                }
            )
    return links
