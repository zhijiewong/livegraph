from livegraph.graph.backend import FakeBackend
from livegraph.mcp.tools import find_callers, find_callees


def test_find_callers_returns_caller_with_edge_provenance():
    row = {
        "qualified_name": "a.py::g", "name": "g", "kind": "function",
        "file": "a.py", "start_line": 1, "end_line": 2,
        "static": True, "runtime": False, "observed_count": 0,
        "call_site_lines": [],
    }
    backend = FakeBackend(rows=[row])
    results = find_callers(backend, project="p", qualified_name="a.py::f")
    assert len(results) == 1
    assert results[0]["caller"]["qualified_name"] == "a.py::g"
    assert results[0]["edge"]["static"] is True
    assert results[0]["edge"]["runtime"] is False
    _q, params = backend.calls[0]
    assert params["provenance"] == "any"


def test_find_callers_passes_provenance_filter():
    backend = FakeBackend(rows=[])
    find_callers(backend, project="p", qualified_name="x",
                 provenance="runtime", limit=5)
    _q, params = backend.calls[0]
    assert params["provenance"] == "runtime"
    assert params["limit"] == 5


def test_find_callees_returns_callee_with_edge_provenance():
    row = {
        "qualified_name": "a.py::h", "name": "h", "kind": "method",
        "file": "a.py", "start_line": 5, "end_line": 6,
        "static": False, "runtime": True, "observed_count": 3,
        "call_site_lines": [12, 17],
    }
    backend = FakeBackend(rows=[row])
    results = find_callees(backend, project="p", qualified_name="a.py::f")
    assert results[0]["callee"]["qualified_name"] == "a.py::h"
    assert results[0]["edge"]["observed_count"] == 3
    assert results[0]["edge"]["call_site_lines"] == [12, 17]
