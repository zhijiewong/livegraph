from typing import Any

from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import semantic_search


class _MockProvider:
    name = "mock-model"
    dimensions = 384
    batch_size = 32

    def __init__(self):
        self.encode_calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        return [[0.5] * 384 for _ in texts]


def test_semantic_search_returns_documented_keys():
    backend = FakeBackend(rows=[
        {"qualified_name": "a.py::f", "name": "f", "kind": "function",
         "file": "a.py", "start_line": 1, "end_line": 5,
         "source": "def f():\n    return 1\n", "score": 0.92},
    ])
    provider = _MockProvider()
    result = semantic_search(backend, project="sample", provider=provider,
                            query="addition")
    assert "results" in result
    assert "model" in result
    assert "embedded_count" in result
    assert "warning" in result
    assert result["model"] == "mock-model"


def test_semantic_search_returns_empty_when_index_missing():
    backend = FakeBackend(rows=[])
    provider = _MockProvider()
    result = semantic_search(backend, project="sample", provider=provider,
                            query="anything")
    assert result["results"] == []
    assert "no embeddings" in (result.get("warning") or "").lower()
    assert provider.encode_calls == []


def test_semantic_search_calls_provider_encode_with_query():
    backend = FakeBackend(rows=[
        {"name": "livegraph_symbol_embeddings", "type": "VECTOR"},
    ])
    provider = _MockProvider()
    semantic_search(backend, project="sample", provider=provider,
                    query="how authentication works")
    assert provider.encode_calls == [["how authentication works"]]


def test_semantic_search_passes_kind_filter():
    backend = FakeBackend(rows=[
        {"name": "livegraph_symbol_embeddings", "type": "VECTOR"},
    ])
    provider = _MockProvider()
    semantic_search(backend, project="sample", provider=provider,
                    query="x", kind="function")
    query_calls = [c for c in backend.calls
                  if "db.index.vector.queryNodes" in c[0]]
    assert query_calls, "expected a vector query call"
    _q, params = query_calls[0]
    assert params["kind"] == "function"


def test_semantic_search_passes_limit():
    backend = FakeBackend(rows=[
        {"name": "livegraph_symbol_embeddings", "type": "VECTOR"},
    ])
    provider = _MockProvider()
    semantic_search(backend, project="sample", provider=provider,
                    query="x", limit=5)
    query_calls = [c for c in backend.calls
                  if "db.index.vector.queryNodes" in c[0]]
    assert query_calls
    _q, params = query_calls[0]
    assert params["limit"] == 5
