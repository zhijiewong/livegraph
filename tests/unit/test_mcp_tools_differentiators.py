from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import runtime_only_calls, dead_static_calls


def test_runtime_only_calls_emits_pairs_with_count():
    row = {
        "caller_qualified_name": "runner.py::run_operation",
        "caller_name": "run_operation", "caller_kind": "function",
        "caller_file": "runner.py", "caller_start_line": 7,
        "caller_end_line": 8,
        "callee_qualified_name": "calculator.py::Calculator.add",
        "callee_name": "add", "callee_kind": "method",
        "callee_file": "calculator.py", "callee_start_line": 6,
        "callee_end_line": 7,
        "observed_count": 4,
    }
    backend = FakeBackend(rows=[row])
    results = runtime_only_calls(backend, project="sample")
    assert len(results) == 1
    assert results[0]["caller"]["qualified_name"] == "runner.py::run_operation"
    assert results[0]["callee"]["qualified_name"] == "calculator.py::Calculator.add"
    assert results[0]["observed_count"] == 4
    _q, params = backend.calls[0]
    assert params["file"] is None
    assert params["limit"] == 100


def test_runtime_only_calls_passes_file_filter():
    backend = FakeBackend(rows=[])
    runtime_only_calls(backend, project="p", file="runner.py", limit=10)
    _q, params = backend.calls[0]
    assert params["file"] == "runner.py"
    assert params["limit"] == 10


def test_dead_static_calls_returns_caller_callee_pairs():
    row = {
        "caller_qualified_name": "a.py::main", "caller_name": "main",
        "caller_kind": "function", "caller_file": "a.py",
        "caller_start_line": 1, "caller_end_line": 5,
        "callee_qualified_name": "a.py::unused", "callee_name": "unused",
        "callee_kind": "function", "callee_file": "a.py",
        "callee_start_line": 10, "callee_end_line": 12,
    }
    backend = FakeBackend(rows=[row])
    results = dead_static_calls(backend, project="p")
    assert results[0]["caller"]["qualified_name"] == "a.py::main"
    assert results[0]["callee"]["qualified_name"] == "a.py::unused"
