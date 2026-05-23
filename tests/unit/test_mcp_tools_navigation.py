from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import find_symbol, get_source


def test_find_symbol_substring_returns_symbols():
    rows = [
        {"qualified_name": "a.py::run_operation", "name": "run_operation",
         "kind": "function", "file": "a.py",
         "start_line": 1, "end_line": 5},
        {"qualified_name": "b.py::run", "name": "run", "kind": "method",
         "file": "b.py", "start_line": 10, "end_line": 20},
    ]
    backend = FakeBackend(rows=rows)
    results = find_symbol(backend, project="sample", query="run")
    assert len(results) == 2
    assert results[0]["qualified_name"] == "a.py::run_operation"
    _q, params = backend.calls[0]
    assert params["project"] == "sample"
    assert params["query"] == "run"
    assert params["exact"] is False
    assert params["limit"] == 50


def test_find_symbol_exact_passes_flag():
    backend = FakeBackend(rows=[])
    find_symbol(backend, project="p", query="run", exact=True, limit=10)
    _q, params = backend.calls[0]
    assert params["exact"] is True
    assert params["limit"] == 10


def test_get_source_returns_full_symbol_with_metadata():
    row = {
        "qualified_name": "a.py::f", "name": "f", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 3,
        "decorators": ["staticmethod"], "source": "def f(): pass",
        "runtime_observed": True, "coverage_pct": 80.0,
    }
    backend = FakeBackend(rows=[row])
    result = get_source(backend, project="p", qualified_name="a.py::f")
    assert result is not None
    assert result["qualified_name"] == "a.py::f"
    assert result["decorators"] == ["staticmethod"]
    assert result["runtime_observed"] is True
    assert result["coverage_pct"] == 80.0


def test_get_source_returns_none_when_missing():
    backend = FakeBackend(rows=[])
    assert get_source(backend, project="p", qualified_name="missing") is None
