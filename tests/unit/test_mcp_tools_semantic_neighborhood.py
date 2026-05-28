from __future__ import annotations

from typing import Any

from livegraph.mcp.tools_neighborhood import semantic_neighborhood


class _FakeBackend:
    """Returns canned responses; matches the pattern used by other unit tests."""

    def __init__(self, responses: dict[str, list[dict[str, Any]]]):
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        for key, rows in self._responses.items():
            if key in cypher:
                return rows
        return []

    def verify(self):
        return None

    def close(self):
        return None


class _FakeProvider:
    name = "fake-model"
    dimensions = 4
    batch_size = 8

    def encode(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _index_exists_rows():
    return [{"name": "livegraph_symbol_embeddings"}]


def _seed_rows():
    return [
        {"qualified_name": "pkg.A.foo", "name": "foo", "kind": "method",
         "file": "pkg/a.py", "start_line": 1, "end_line": 3,
         "source": "def foo():\n    pass\n", "score": 0.9},
        {"qualified_name": "pkg.B.bar", "name": "bar", "kind": "method",
         "file": "pkg/b.py", "start_line": 1, "end_line": 3,
         "source": "def bar():\n    pass\n", "score": 0.7},
    ]


def test_missing_index_returns_warning_no_calls_beyond_index_check():
    backend = _FakeBackend({"SHOW INDEXES": []})
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="anything",
    )
    assert result["results"] == []
    assert "embed" in result["warning"]
    assert all("CALLS" not in c[0] for c in backend.calls)


def test_invalid_kind_returns_warning_no_db_calls():
    backend = _FakeBackend({"SHOW INDEXES": _index_exists_rows()})
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", kind="nonsense",
    )
    assert result["results"] == []
    assert "kind" in result["warning"].lower()


def test_default_include_returns_callers_callees_tests_grouped_per_seed():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "pkg.api.handle_foo",
             "provenance": "runtime"},
        ],
        "MATCH (target)-[r:CALLS]->(callee)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "builtins.int",
             "provenance": "static"},
        ],
        "(test:Test)-[:COVERS]->(target)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "tests.test_foo.test_basic"},
        ],
        "RETURN count": [{"n": 50}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", limit=2,
    )
    assert result["warning"] is None
    assert len(result["results"]) == 2
    foo = result["results"][0]
    assert foo["qualified_name"] == "pkg.A.foo"
    assert foo["callers"] == [
        {"qualified_name": "pkg.api.handle_foo", "provenance": "runtime"},
    ]
    assert foo["callees"] == [
        {"qualified_name": "builtins.int", "provenance": "static"},
    ]
    assert foo["tests"] == [
        {"qualified_name": "tests.test_foo.test_basic"},
    ]
    bar = result["results"][1]
    assert bar["callers"] == []
    assert bar["callees"] == []
    assert bar["tests"] == []


def test_include_subset_skips_other_expansion_queries():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [],
        "RETURN count": [{"n": 0}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", include=["callers"],
    )
    cyphers = [c[0] for c in backend.calls]
    assert any("MATCH (caller)-[r:CALLS]->(target)" in c for c in cyphers)
    assert not any("MATCH (target)-[r:CALLS]->(callee)" in c for c in cyphers)
    assert not any("(test:Test)-[:COVERS]->(target)" in c for c in cyphers)
    for r in result["results"]:
        assert "callers" in r
        assert "callees" not in r
        assert "tests" not in r


def test_limit_and_per_seed_limit_are_clamped():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [],
        "MATCH (target)-[r:CALLS]->(callee)": [],
        "(test:Test)-[:COVERS]->(target)": [],
        "RETURN count": [{"n": 0}],
    })
    semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", limit=999, per_seed_limit=999,
    )
    vec_call = [c for c in backend.calls
                if "db.index.vector.queryNodes" in c[0]][0]
    assert vec_call[1]["limit"] == 50
    exp_calls = [c for c in backend.calls if "$per_seed_limit" in c[0]]
    assert exp_calls and all(c[1]["per_seed_limit"] == 50 for c in exp_calls)


def test_min_score_filters_seeds():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": _seed_rows(),
        "MATCH (caller)-[r:CALLS]->(target)": [],
        "MATCH (target)-[r:CALLS]->(callee)": [],
        "(test:Test)-[:COVERS]->(target)": [],
        "RETURN count": [{"n": 0}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", min_score=0.8,
    )
    assert [r["qualified_name"] for r in result["results"]] == ["pkg.A.foo"]


def test_provenance_forwarded_unchanged():
    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
        "db.index.vector.queryNodes": [_seed_rows()[0]],
        "MATCH (caller)-[r:CALLS]->(target)": [
            {"seed_qn": "pkg.A.foo",
             "qualified_name": "pkg.api.handle_foo",
             "provenance": "both"},
        ],
        "MATCH (target)-[r:CALLS]->(callee)": [],
        "(test:Test)-[:COVERS]->(target)": [],
        "RETURN count": [{"n": 1}],
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_FakeProvider(),
        query="q", limit=1, include=["callers"],
    )
    assert result["results"][0]["callers"] == [
        {"qualified_name": "pkg.api.handle_foo", "provenance": "both"},
    ]


def test_missing_semantic_extra_returns_install_hint():
    from livegraph.semantic.provider import EmbeddingExtraMissing

    class _BoomProvider:
        name = "boom"
        dimensions = 4
        batch_size = 1

        def encode(self, texts):
            raise EmbeddingExtraMissing("install livegraph[semantic]")

    backend = _FakeBackend({
        "SHOW INDEXES": _index_exists_rows(),
    })
    result = semantic_neighborhood(
        backend, project="p", provider=_BoomProvider(), query="q",
    )
    assert result["results"] == []
    assert "semantic" in result["warning"].lower()
