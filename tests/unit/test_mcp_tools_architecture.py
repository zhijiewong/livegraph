from __future__ import annotations

from typing import Any

from livegraph.mcp.tools_architecture import find_cycles


class _FakeBackend:
    def __init__(self, rows=None):
        # Either a single list (returned for every call) or a dict keyed
        # by substring-of-cypher.
        self._rows = rows or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if isinstance(self._rows, dict):
            for key, rows in self._rows.items():
                if key in cypher:
                    return rows
            return []
        return self._rows

    def verify(self): return None
    def close(self): return None


def test_find_cycles_invalid_scope_returns_warning():
    backend = _FakeBackend([])
    out = find_cycles(backend, project="p", scope="nonsense")
    assert out["cycles"] == []
    assert "scope" in out["warning"].lower()
    assert backend.calls == []


def test_find_cycles_invalid_provenance_returns_warning():
    backend = _FakeBackend([])
    out = find_cycles(backend, project="p", provenance="bogus")
    assert out["cycles"] == []
    assert "provenance" in out["warning"].lower()


def test_find_cycles_call_scope_returns_scc_of_cycle():
    backend = _FakeBackend([
        {"source": "pkg.a.foo", "target": "pkg.b.bar"},
        {"source": "pkg.b.bar", "target": "pkg.a.foo"},
        {"source": "pkg.c.baz", "target": "pkg.d.qux"},
    ])
    out = find_cycles(backend, project="p", scope="call")
    assert out["warning"] is None
    assert len(out["cycles"]) == 1
    assert sorted(out["cycles"][0]["nodes"]) == ["pkg.a.foo", "pkg.b.bar"]
    assert out["cycles"][0]["size"] == 2


def test_find_cycles_min_size_drops_self_loops():
    backend = _FakeBackend([
        {"source": "pkg.a.foo", "target": "pkg.a.foo"},
        {"source": "pkg.b.bar", "target": "pkg.c.baz"},
        {"source": "pkg.c.baz", "target": "pkg.b.bar"},
    ])
    out = find_cycles(backend, project="p", scope="call", min_size=2)
    qns_in_cycles = [n for c in out["cycles"] for n in c["nodes"]]
    assert "pkg.a.foo" not in qns_in_cycles
    assert "pkg.b.bar" in qns_in_cycles
    assert "pkg.c.baz" in qns_in_cycles


def test_find_cycles_min_size_one_includes_self_loops():
    backend = _FakeBackend([
        {"source": "pkg.a.foo", "target": "pkg.a.foo"},
    ])
    out = find_cycles(backend, project="p", scope="call", min_size=1)
    assert len(out["cycles"]) == 1
    assert out["cycles"][0]["nodes"] == ["pkg.a.foo"]


def test_find_cycles_orders_by_size_desc():
    backend = _FakeBackend([
        {"source": "a", "target": "b"},
        {"source": "b", "target": "a"},
        {"source": "c", "target": "d"},
        {"source": "d", "target": "e"},
        {"source": "e", "target": "c"},
    ])
    out = find_cycles(backend, project="p", scope="call")
    sizes = [c["size"] for c in out["cycles"]]
    assert sizes == [3, 2]


def test_find_cycles_limit_clamps_to_100():
    backend = _FakeBackend([])
    out = find_cycles(backend, project="p", scope="call", limit=9999)
    assert isinstance(out["cycles"], list)


def test_find_cycles_module_scope_uses_imports_query():
    backend = _FakeBackend([
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "a.py"},
    ])
    out = find_cycles(backend, project="p", scope="module")
    assert len(backend.calls) == 1
    cypher = backend.calls[0][0]
    assert ":IMPORTS" in cypher
    assert ":CALLS" not in cypher
    assert sorted(out["cycles"][0]["nodes"]) == ["a.py", "b.py"]


def test_find_cycles_call_scope_provenance_static_filters_runtime():
    backend = _FakeBackend([])
    find_cycles(backend, project="p", scope="call", provenance="static")
    cypher = backend.calls[0][0]
    assert "c.static" in cypher
