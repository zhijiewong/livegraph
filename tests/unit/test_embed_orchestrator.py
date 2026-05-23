import hashlib
from typing import Any

import pytest

from livegraph.semantic.embed import (
    EmbedSummary, embed_project, INDEX_NAME,
)
from livegraph.semantic.provider import (
    EmbeddingDimensionMismatch,
)


class _MockProvider:
    """Deterministic stand-in for LocalSTProvider."""

    def __init__(self, name="mock-model", dimensions=384, batch_size=32):
        self.name = name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.encode_calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        return [[float(i)] * self.dimensions for i, _ in enumerate(texts)]


class _QueuedBackend:
    """Per-call canned responses (mirrors Phase 4's pattern)."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify(self): return None
    def close(self): return None

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_embed_all_new_writes_every_candidate():
    provider = _MockProvider()
    src_a = "def f():\n    return 1\n"
    src_b = "def g():\n    return 2\n"
    backend = _QueuedBackend([
        [],   # _read_existing_index_dimensions: no index yet
        [     # candidates query
            {"qn": "a.py::f", "source": src_a,
             "prior_hash": None, "prior_model": None},
            {"qn": "a.py::g", "source": src_b,
             "prior_hash": None, "prior_model": None},
        ],
        [],   # write batch
        [],   # CREATE INDEX
        [],   # CALL db.awaitIndexes()
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 2
    assert summary.unchanged == 0
    assert summary.skipped == 0
    assert provider.encode_calls == [[src_a, src_b]]
    write_calls = [c for c in backend.calls if "SET s:Symbol" in c[0]]
    assert len(write_calls) == 1
    rows = write_calls[0][1]["rows"]
    assert {r["qn"] for r in rows} == {"a.py::f", "a.py::g"}
    assert rows[0]["model"] == "mock-model"


def test_embed_no_op_when_hashes_unchanged():
    provider = _MockProvider()
    src = "def f():\n    return 1\n"
    h = _hash(src)
    backend = _QueuedBackend([
        [{"dimensions": 384}],
        [
            {"qn": "a.py::f", "source": src,
             "prior_hash": h, "prior_model": "mock-model"},
        ],
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 0
    assert summary.unchanged == 1
    assert provider.encode_calls == []
    assert not any("SET s:Symbol" in c[0] for c in backend.calls)


def test_embed_only_changed_source():
    provider = _MockProvider()
    src_old = "def f():\n    return 1\n"
    src_new = "def f():\n    return 2\n"
    src_b = "def g():\n    return 3\n"
    h_b = _hash(src_b)
    backend = _QueuedBackend([
        [{"dimensions": 384}],
        [
            {"qn": "a.py::f", "source": src_new,
             "prior_hash": _hash(src_old), "prior_model": "mock-model"},
            {"qn": "a.py::g", "source": src_b,
             "prior_hash": h_b, "prior_model": "mock-model"},
        ],
        [],
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 1
    assert summary.unchanged == 1
    assert provider.encode_calls == [[src_new]]


def test_embed_skips_empty_source():
    provider = _MockProvider()
    backend = _QueuedBackend([
        [{"dimensions": 384}],
        [
            {"qn": "a.py::f", "source": "",
             "prior_hash": None, "prior_model": None},
            {"qn": "a.py::g", "source": "def g(): pass\n",
             "prior_hash": None, "prior_model": None},
        ],
        [],
    ])

    summary = embed_project(backend, project="sample", provider=provider)

    assert summary.embedded == 1
    assert summary.skipped == 1
    assert provider.encode_calls == [["def g(): pass\n"]]


def test_embed_refuses_dimension_mismatch():
    provider = _MockProvider(dimensions=768)
    backend = _QueuedBackend([
        [{"dimensions": 384}],
    ])

    with pytest.raises(EmbeddingDimensionMismatch) as exc:
        embed_project(backend, project="sample", provider=provider)
    assert exc.value.existing == 384
    assert exc.value.new == 768
    assert not any("MATCH" in c[0] and "qualified_name" in c[0]
                   for c in backend.calls)


def test_embed_rebuild_clears_then_re_embeds():
    provider = _MockProvider()
    src = "def f():\n    return 1\n"
    backend = _QueuedBackend([
        [],   # DROP INDEX
        [],   # REMOVE label + properties
        [],   # _read_existing_index_dimensions: gone
        [{"qn": "a.py::f", "source": src,
          "prior_hash": None, "prior_model": None}],
        [],   # write batch
        [],   # CREATE INDEX
        [],   # CALL db.awaitIndexes()
    ])

    summary = embed_project(backend, project="sample",
                            provider=provider, rebuild=True)

    assert summary.embedded == 1
    assert "DROP" in backend.calls[0][0]
    assert "REMOVE" in backend.calls[1][0]
    assert provider.encode_calls == [[src]]


def test_embed_creates_vector_index_with_model_dimensions():
    provider = _MockProvider(dimensions=768)
    backend = _QueuedBackend([
        [],
        [{"qn": "a.py::f", "source": "x = 1\n",
          "prior_hash": None, "prior_model": None}],
        [],
        [],
        [],
    ])

    embed_project(backend, project="sample", provider=provider)

    create_calls = [c for c in backend.calls if "CREATE VECTOR INDEX" in c[0]]
    assert create_calls, "expected a CREATE VECTOR INDEX call"
    create_cypher, _params = create_calls[0]
    assert "`vector.dimensions`: 768" in create_cypher
    assert INDEX_NAME in create_cypher
