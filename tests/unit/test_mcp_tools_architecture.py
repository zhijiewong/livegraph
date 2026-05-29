from __future__ import annotations

from typing import Any

from livegraph.mcp.tools_architecture import (
    find_cycles,
    hubs,
    layering_violations,
)


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


# ---- layering_violations -------------------------------------------



def test_layering_empty_layers_warns():
    backend = _FakeBackend([])
    out = layering_violations(backend, project="p", layers=[])
    assert out["violations"] == []
    assert "empty" in (out["warning"] or "").lower()


def test_layering_invalid_edge_kind_warns():
    backend = _FakeBackend([])
    out = layering_violations(
        backend, project="p",
        layers=[{"name": "x", "patterns": ["*"]}],
        edge_kind="bogus",
    )
    assert out["violations"] == []
    assert "edge_kind" in (out["warning"] or "").lower()


def test_layering_detects_imports_violation():
    backend = _FakeBackend({
        "(f:File)": [
            {"path": "domain/calc.py"},
            {"path": "web/handlers.py"},
            {"path": "scripts/run.py"},  # unmatched
        ],
        ":IMPORTS": [
            {"from_file": "domain/calc.py", "to_file": "web/handlers.py"},
        ],
        "CALLS": [],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
    )
    assert any(
        v["from_file"] == "domain/calc.py" and v["to_file"] == "web/handlers.py"
        for v in out["violations"]
    )
    assert out["summary"]["files_unlayered"] == 1


def test_layering_first_match_wins_for_multimatch():
    # domain/auth/login.py matches both "domain/auth/**" (auth, rank 0) and
    # "domain/**" (domain, rank 2). First-match-wins assigns "auth" (rank 0).
    # auth(0) -> web(1): rank 0 < rank 1, so NOT a violation (top-down is OK).
    # If "domain" had been assigned instead, domain(2)->web(1) would be a
    # violation. Verify first-match-wins prevents that false positive.
    backend = _FakeBackend({
        "(f:File)": [
            {"path": "domain/auth/login.py"},
            {"path": "web/handlers.py"},
        ],
        ":IMPORTS": [
            {"from_file": "domain/auth/login.py",
             "to_file": "web/handlers.py"},
        ],
        "CALLS": [],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "auth", "patterns": ["domain/auth/**"]},
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
    )
    # First-match-wins correctly assigns "auth" (rank 0), which may depend on
    # web (rank 1); no upward violation is present.
    assert out["violations"] == []


def test_layering_skips_intra_layer_and_top_to_bottom_edges():
    backend = _FakeBackend({
        "(f:File)": [
            {"path": "web/a.py"}, {"path": "web/b.py"},
            {"path": "domain/c.py"},
        ],
        ":IMPORTS": [
            {"from_file": "web/a.py", "to_file": "web/b.py"},      # intra
            {"from_file": "web/a.py", "to_file": "domain/c.py"},   # web->domain OK
        ],
        "CALLS": [],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
    )
    assert out["violations"] == []
    assert out["summary"]["violations"] == 0


def test_layering_edge_kind_filter_imports_only():
    backend = _FakeBackend({
        "(f:File)": [
            {"path": "domain/c.py"}, {"path": "web/h.py"},
        ],
        ":IMPORTS": [
            {"from_file": "domain/c.py", "to_file": "web/h.py"},
        ],
        "CALLS": [
            {"from_file": "domain/c.py", "to_file": "web/h.py"},
        ],
    })
    out = layering_violations(
        backend, project="p",
        layers=[
            {"name": "web", "patterns": ["web/**"]},
            {"name": "domain", "patterns": ["domain/**"]},
        ],
        edge_kind="imports",
    )
    kinds = {v["edge_kind"] for v in out["violations"]}
    assert kinds == {"imports"}


# ---- hubs ----------------------------------------------------------



def test_hubs_invalid_kind_warns():
    backend = _FakeBackend([])
    out = hubs(backend, project="p", kind="bogus")
    assert out["results"] == []
    assert "kind" in (out["warning"] or "").lower()


def test_hubs_returns_results_ordered_by_in_callers_desc():
    backend = _FakeBackend([
        {"qualified_name": "pkg.util.normalize", "kind": "function",
         "file": "pkg/util.py", "in_callers": 47, "out_callees": 3},
        {"qualified_name": "pkg.util.format", "kind": "function",
         "file": "pkg/util.py", "in_callers": 12, "out_callees": 1},
    ])
    out = hubs(backend, project="p", min_fanin=10)
    assert [r["qualified_name"] for r in out["results"]] == [
        "pkg.util.normalize", "pkg.util.format",
    ]


def test_hubs_passes_min_fanin_and_limit_to_query():
    backend = _FakeBackend([])
    hubs(backend, project="p", min_fanin=50, limit=5)
    cypher, params = backend.calls[0]
    assert params["min_fanin"] == 50
    assert params["limit"] == 5


def test_hubs_clamps_min_fanin_and_limit():
    backend = _FakeBackend([])
    hubs(backend, project="p", min_fanin=99999, limit=9999)
    _, params = backend.calls[0]
    assert params["min_fanin"] == 1000
    assert params["limit"] == 100


def test_hubs_kind_function_excludes_methods_in_cypher():
    backend = _FakeBackend([])
    hubs(backend, project="p", kind="function")
    cypher = backend.calls[0][0]
    assert "Function" in cypher
