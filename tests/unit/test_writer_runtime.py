from livegraph.graph.backend import FakeBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import CoverageRecord, RuntimeCall, TestResult


def test_write_runtime_calls_sets_runtime_provenance():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_runtime_calls(
        [RuntimeCall("a.py::f", "a.py::g", "t.py::test", 0)],
        counts={("a.py::f", "a.py::g"): 4},
    )
    query, params = backend.calls[0]
    assert "MERGE" in query and ":CALLS" in query
    assert params["rows"][0]["runtime"] is True
    assert params["rows"][0]["observed_count"] == 4


def test_write_test_results_adds_test_label():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_test_results(
        [TestResult("t.py::test_x", "passed", 0.02)])
    query, _params = backend.calls[0]
    assert ":Test" in query and "test_outcome" in query


def test_write_coverage_emits_covers_edges():
    backend = FakeBackend()
    GraphWriter(backend, batch_size=100).write_coverage(
        [CoverageRecord("t.py::test", "a.py::f", 3, 4)])
    query, params = backend.calls[0]
    assert ":COVERS" in query
    assert params["rows"][0]["coverage_pct"] == 75.0
