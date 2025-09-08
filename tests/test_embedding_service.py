# tests/test_embedding_service.py
import numpy as np
import pytest

from app.services import embedding_service as es


def test_search_returns_k(monkeypatch):
    # заменяем faiss index и модель
    class DummyModel:
        def encode(self, texts):
            return np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

    class DummyIndex:
        def search(self, arr, k):
            return np.array([[0.1, 0.2]]), np.array([[0, 1]])

    monkeypatch.setattr(es, "model", DummyModel())
    monkeypatch.setattr(es, "index", DummyIndex())
    monkeypatch.setattr(es, "metadata", ["a", "b", "c"])

    out = es.search("q", k=2)
    assert out == ["a", "b"]
