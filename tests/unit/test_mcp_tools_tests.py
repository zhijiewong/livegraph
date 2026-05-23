from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import tests_for, untested_symbols


def test_tests_for_returns_test_with_coverage():
    row = {
        "qualified_name": "test_a.py::test_x", "name": "test_x",
        "kind": "function", "file": "test_a.py",
        "start_line": 1, "end_line": 3,
        "test_outcome": "passed", "test_duration": 0.02,
        "lines_covered": 3, "lines_total": 4, "coverage_pct": 75.0,
    }
    backend = FakeBackend(rows=[row])
    results = tests_for(backend, project="p",
                       qualified_name="a.py::f")
    assert results[0]["test"]["qualified_name"] == "test_a.py::test_x"
    assert results[0]["test"]["test_outcome"] == "passed"
    assert results[0]["test"]["test_duration"] == 0.02
    assert results[0]["coverage_pct"] == 75.0
    assert results[0]["lines_covered"] == 3
    assert results[0]["lines_total"] == 4


def test_untested_symbols_passes_kind_and_file_filters():
    row = {
        "qualified_name": "a.py::dead", "name": "dead",
        "kind": "function", "file": "a.py",
        "start_line": 1, "end_line": 2,
    }
    backend = FakeBackend(rows=[row])
    results = untested_symbols(backend, project="p", file="a.py",
                               kind="function", limit=10)
    assert results[0]["qualified_name"] == "a.py::dead"
    _q, params = backend.calls[0]
    assert params["file"] == "a.py"
    assert params["kind"] == "function"
    assert params["limit"] == 10


def test_untested_symbols_kind_any_filters_to_function_or_method():
    backend = FakeBackend(rows=[])
    untested_symbols(backend, project="p", kind="any")
    _q, params = backend.calls[0]
    assert params["kind"] == "any"


def test_tests_for_passes_limit_parameter():
    backend = FakeBackend(rows=[])
    from livegraph.mcp.tools import tests_for
    tests_for(backend, project="p", qualified_name="x", limit=5)
    _q, params = backend.calls[0]
    assert params["limit"] == 5
