from typing import Any

from livegraph.mcp.tools import change_impact


class _QueuedBackend:
    """Test backend that returns a different canned response per execute call."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify(self) -> None:
        return None

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if not self._responses:
            return []
        return self._responses.pop(0)

    def close(self) -> None:
        return None


_SIMPLE_DIFF = (
    "--- a/calculator.py\n"
    "+++ b/calculator.py\n"
    "@@ -5,3 +5,3 @@ class Calculator:\n"
    "     def add(self, a, b):\n"
    "-        return a + b\n"
    "+        return a + b + 0\n"
)


def test_change_impact_assembles_changed_impacted_tests():
    backend = _QueuedBackend([
        [{
            "qualified_name": "calculator.py::Calculator.add",
            "name": "add", "kind": "method", "file": "calculator.py",
            "start_line": 5, "end_line": 7,
            "runtime_observed": True, "coverage_pct": 100.0,
        }],
        [{
            "qualified_name": "runner.py::run_operation",
            "name": "run_operation", "kind": "function",
            "file": "runner.py", "start_line": 7, "end_line": 8,
            "runtime_observed": True, "coverage_pct": 100.0,
            "reached_via": [{
                "via": "calculator.py::Calculator.add",
                "depth": 1,
                "edges": [{"static": False, "runtime": True}],
            }],
        }],
        [{
            "qualified_name": "test_calculator.py::test_main", "name": "test_main",
            "kind": "function", "file": "test_calculator.py",
            "start_line": 10, "end_line": 12,
            "test_outcome": "passed",
            "covers_symbols": [
                "calculator.py::Calculator.add", "runner.py::run_operation",
            ],
            "avg_coverage_pct": 100.0,
        }],
    ])

    result = change_impact(backend, project="sample", diff=_SIMPLE_DIFF)

    assert result["changed"][0]["qualified_name"] == "calculator.py::Calculator.add"
    assert result["impacted"][0]["qualified_name"] == "runner.py::run_operation"
    assert result["impacted"][0]["reached_via"][0]["depth"] == 1
    assert result["impacted"][0]["reached_via"][0]["edges"][0]["runtime"] is True
    assert result["tests_to_run"][0]["qualified_name"] == "test_calculator.py::test_main"
    assert result["unmatched_files"] == []
    assert result["stats"] == {
        "changed_files": 1,
        "changed_symbols": 1,
        "impacted_symbols": 1,
        "tests_to_run": 1,
        "max_depth_reached": 1,
    }


def test_change_impact_reports_unmatched_files_when_query_a_returns_nothing():
    backend = _QueuedBackend([[], [], []])
    result = change_impact(backend, project="p", diff=_SIMPLE_DIFF)
    assert result["unmatched_files"] == ["calculator.py"]
    assert result["changed"] == []
    assert result["impacted"] == []
    assert result["tests_to_run"] == []
    assert result["stats"]["changed_files"] == 1
    assert result["stats"]["changed_symbols"] == 0


def test_change_impact_clamps_max_depth_to_range():
    _changed_row = {
        "qualified_name": "calculator.py::Calculator.add",
        "name": "add", "kind": "method", "file": "calculator.py",
        "start_line": 5, "end_line": 7,
        "runtime_observed": True, "coverage_pct": 100.0,
    }
    backend = _QueuedBackend([[_changed_row], [], []])
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, max_depth=999)
    query_b = backend.calls[1][0]
    assert "CALLS*1..20" in query_b

    backend = _QueuedBackend([[_changed_row], [], []])
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, max_depth=0)
    query_b = backend.calls[1][0]
    assert "CALLS*1..1" in query_b


def test_change_impact_passes_provenance_filter():
    _changed_row = {
        "qualified_name": "calculator.py::Calculator.add",
        "name": "add", "kind": "method", "file": "calculator.py",
        "start_line": 5, "end_line": 7,
        "runtime_observed": True, "coverage_pct": 100.0,
    }
    backend = _QueuedBackend([[_changed_row], [], []])
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, provenance="runtime")
    _q, params = backend.calls[1]
    assert params["provenance"] == "runtime"


def test_change_impact_passes_limit_to_impacted_query():
    _changed_row = {
        "qualified_name": "calculator.py::Calculator.add",
        "name": "add", "kind": "method", "file": "calculator.py",
        "start_line": 5, "end_line": 7,
        "runtime_observed": True, "coverage_pct": 100.0,
    }
    backend = _QueuedBackend([[_changed_row], [], []])
    change_impact(backend, project="p", diff=_SIMPLE_DIFF, limit=7)
    _q, params = backend.calls[1]
    assert params["limit"] == 7


def test_change_impact_with_empty_diff_returns_all_empty():
    backend = _QueuedBackend([])
    result = change_impact(backend, project="p", diff="")
    assert result["changed"] == []
    assert result["impacted"] == []
    assert result["tests_to_run"] == []
    assert result["unmatched_files"] == []
    assert result["stats"]["changed_files"] == 0
    assert backend.calls == []
