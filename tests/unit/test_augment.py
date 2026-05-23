from livegraph.augment import augment_from_observations
from livegraph.graph.backend import FakeBackend


def test_augment_writes_calls_tests_and_coverage():
    definitions_rows = [
        {"qualified_name": "calc.py::add", "file": "calc.py",
         "start_line": 1, "end_line": 2, "kind": "function"},
    ]
    backend = FakeBackend(rows=definitions_rows)
    observations = {
        "root": "/tmp/proj",
        "runtime_calls": [
            {"caller_qn": "calc.py::total", "callee_qn": "calc.py::add",
             "test_qn": "calc.py::test_total", "observed_count": 2},
        ],
        "tests": [
            {"qualified_name": "calc.py::test_total", "outcome": "passed",
             "duration": 0.01},
        ],
        "coverage": [
            {"test_context": "calc.py::test_total", "file": "calc.py",
             "lines": [1, 2]},
        ],
    }
    summary = augment_from_observations(observations, backend, batch_size=100)
    assert summary.runtime_call_edges == 1
    assert summary.tests == 1
    assert summary.coverage_edges == 1
    issued = " ".join(q for q, _ in backend.calls)
    assert ":CALLS" in issued and ":Test" in issued and ":COVERS" in issued
