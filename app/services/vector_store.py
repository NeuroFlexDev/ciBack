from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

import faiss
import numpy as np


@dataclass(frozen=True)
class VectorRecord:
    embedding_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchFilters:
    owner_id: int
    course_id: int
    allowed_chunk_ids: frozenset[int]


@dataclass(frozen=True)
class VectorMatch:
    embedding_id: str
    text: str
    score: float
    metadata: dict[str, Any]


class VectorStore(Protocol):
    def replace_document(
        self, document_id: int, document_version: int, records: list[VectorRecord]
    ) -> list[str]: ...

    def delete_document(self, document_id: int) -> None: ...

    def search(
        self, query: str, filters: VectorSearchFilters, limit: int
    ) -> list[VectorMatch]: ...


class FaissVectorStore:
    """In-memory demo vector store; persisted chunks remain the source of truth."""

    def __init__(self, model_provider: Callable[[], Any]):
        self._model_provider = model_provider
        self._entries: list[tuple[VectorRecord, np.ndarray]] = []
        self._lock = RLock()

    def replace_document(
        self, document_id: int, document_version: int, records: list[VectorRecord]
    ) -> list[str]:
        model = self._model_provider()
        if model is None:
            raise RuntimeError("Embedding model is unavailable")

        normalized_records = [
            VectorRecord(
                embedding_id=record.embedding_id,
                text=record.text,
                metadata={
                    **record.metadata,
                    "document_id": document_id,
                    "document_version": document_version,
                },
            )
            for record in records
        ]
        texts = [record.text for record in normalized_records]
        vectors = (
            np.asarray(model.encode(texts), dtype=np.float32)
            if texts
            else np.empty((0, 0), dtype=np.float32)
        )
        if texts and (vectors.ndim != 2 or len(vectors) != len(records)):
            raise RuntimeError("Embedding model returned an invalid vector batch")

        with self._lock:
            # A document has one active version in the demo index. Reindexing also
            # deactivates any older version that might still be present in memory.
            self._entries = [
                entry
                for entry in self._entries
                if entry[0].metadata.get("document_id") != document_id
            ]
            self._entries.extend(
                (record, vector)
                for record, vector in zip(normalized_records, vectors, strict=True)
            )
        return [record.embedding_id for record in normalized_records]

    def delete_document(self, document_id: int) -> None:
        with self._lock:
            self._entries = [
                entry
                for entry in self._entries
                if entry[0].metadata.get("document_id") != document_id
            ]

    def search(
        self, query: str, filters: VectorSearchFilters, limit: int
    ) -> list[VectorMatch]:
        if not query.strip() or limit <= 0 or not filters.allowed_chunk_ids:
            return []
        # ACL and scope are applied before the candidate FAISS index is built.
        with self._lock:
            candidates = [
                entry
                for entry in self._entries
                if entry[0].metadata.get("owner_id") == filters.owner_id
                and entry[0].metadata.get("course_id") == filters.course_id
                and entry[0].metadata.get("chunk_id") in filters.allowed_chunk_ids
            ]
        if not candidates:
            return []

        model = self._model_provider()
        if model is None:
            raise RuntimeError("Embedding model is unavailable")

        query_vector = np.asarray(model.encode([query]), dtype=np.float32)
        vectors = np.asarray([entry[1] for entry in candidates], dtype=np.float32)
        if query_vector.ndim != 2 or query_vector.shape[1] != vectors.shape[1]:
            raise RuntimeError("Embedding dimensions do not match")

        candidate_index = faiss.IndexFlatL2(vectors.shape[1])
        candidate_index.add(vectors)
        distances, indices = candidate_index.search(
            query_vector, min(limit, len(candidates))
        )
        matches = []
        for distance, candidate_index_value in zip(
            distances[0], indices[0], strict=True
        ):
            if candidate_index_value < 0:
                continue
            record = candidates[int(candidate_index_value)][0]
            matches.append(
                VectorMatch(
                    embedding_id=record.embedding_id,
                    text=record.text,
                    score=1.0 / (1.0 + float(distance)),
                    metadata=dict(record.metadata),
                )
            )
        return matches
