from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import imports, graph_status


def test_imports_out_returns_file_and_module_targets():
    rows = [
        {"target": "pkg/sub.py", "kind": "file",
         "raw": "from pkg.sub import x", "line": 1},
        {"target": "os", "kind": "stdlib", "raw": "import os", "line": 2},
    ]
    backend = FakeBackend(rows=rows)
    results = imports(backend, project="p", file="a.py", direction="out")
    assert results[0]["target"] == "pkg/sub.py"
    assert results[0]["kind"] == "file"
    assert results[1]["kind"] == "stdlib"


def test_imports_in_returns_source_files():
    backend = FakeBackend(rows=[
        {"source_file": "main.py", "raw": "from lib import x", "line": 4},
    ])
    results = imports(backend, project="p", file="lib.py", direction="in")
    assert results[0]["source_file"] == "main.py"
    assert results[0]["raw"] == "from lib import x"


def test_graph_status_summarizes_counts():
    rows = [{
        "project": "sample", "files": 3, "classes": 1,
        "functions": 5, "methods": 2, "tests": 3,
        "calls_total": 7, "calls_runtime_only": 1,
        "calls_static_only": 4, "calls_both": 2,
    }]
    backend = FakeBackend(rows=rows)
    result = graph_status(backend, project="sample")
    assert result["project"] == "sample"
    assert result["files"] == 3
    assert result["calls_runtime_only"] == 1


def test_graph_status_handles_empty_graph():
    backend = FakeBackend(rows=[])
    result = graph_status(backend, project="empty")
    assert result["project"] == "empty"
    assert result["files"] == 0
    assert result["calls_total"] == 0


def test_imports_out_passes_limit_parameter():
    backend = FakeBackend(rows=[])
    from livegraph.mcp.tools import imports
    imports(backend, project="p", file="a.py", direction="out", limit=5)
    _q, params = backend.calls[0]
    assert params["limit"] == 5
