# tests/test_external_sources.py
import pytest

from app.services.external_sources import aggregated_search


def test_aggregated_search_empty(monkeypatch):
    # глушим сеть
    monkeypatch.setattr("app.services.external_sources.arxiv_search", lambda q: ["a"])
    monkeypatch.setattr("app.services.external_sources.crossref_search", lambda q: ["b"])
    monkeypatch.setattr("app.services.external_sources.openalex_search", lambda q: ["c"])
    res = aggregated_search("q", source="all", lang="ru")
    assert res == ["a", "b", "c"]
