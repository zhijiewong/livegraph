"""End-to-end Cypher tests for the MCP tool functions."""
import pytest

from livegraph.mcp import tools

pytestmark = pytest.mark.integration


def test_find_symbol_substring_against_real_graph(ingested_sample):
    backend, project = ingested_sample
    results = tools.find_symbol(backend, project, query="run")
    qns = {r["qualified_name"] for r in results}
    assert "runner.py::run_operation" in qns


def test_get_source_returns_definition(ingested_sample):
    backend, project = ingested_sample
    result = tools.get_source(backend, project,
                              qualified_name="calculator.py::Calculator.add")
    assert result is not None
    assert result["name"] == "add"
    assert result["kind"] == "method"
    assert "def add" in result["source"]


def test_get_source_missing_returns_none(ingested_sample):
    backend, project = ingested_sample
    assert tools.get_source(backend, project,
                            qualified_name="nope.py::nope") is None


def test_find_callees_finds_static_call_in_main(ingested_sample):
    backend, project = ingested_sample
    results = tools.find_callees(backend, project,
                                 qualified_name="runner.py::main")
    callee_qns = {r["callee"]["qualified_name"] for r in results}
    assert "runner.py::run_operation" in callee_qns


def test_find_callers_provenance_filter(ingested_sample):
    backend, project = ingested_sample
    runtime_only = tools.find_callers(
        backend, project,
        qualified_name="calculator.py::Calculator.add",
        provenance="runtime",
    )
    assert any(r["caller"]["qualified_name"] == "runner.py::run_operation"
               for r in runtime_only)


def test_runtime_only_calls_finds_dynamic_dispatch(ingested_sample):
    """The differentiator. If this passes, livegraph's premise is intact."""
    backend, project = ingested_sample
    results = tools.runtime_only_calls(backend, project)
    pairs = {(r["caller"]["qualified_name"],
              r["callee"]["qualified_name"]) for r in results}
    assert ("runner.py::run_operation",
            "calculator.py::Calculator.add") in pairs


def test_dead_static_calls_returns_static_only_edges(ingested_sample):
    backend, project = ingested_sample
    results = tools.dead_static_calls(backend, project)
    assert isinstance(results, list)


def test_tests_for_returns_tests_with_coverage(ingested_sample):
    backend, project = ingested_sample
    results = tools.tests_for(
        backend, project,
        qualified_name="calculator.py::Calculator.add",
    )
    assert results, "Calculator.add should be covered by at least one test"
    assert all("test_outcome" in r["test"] for r in results)
    assert all(0.0 <= r["coverage_pct"] <= 100.0 for r in results)


def test_untested_symbols_against_real_graph(ingested_sample):
    backend, project = ingested_sample
    results = tools.untested_symbols(backend, project, kind="function")
    assert isinstance(results, list)


def test_imports_out_returns_internal_and_external(ingested_sample):
    backend, project = ingested_sample
    results = tools.imports(backend, project, file="runner.py",
                            direction="out")
    targets = {r["target"] for r in results}
    assert "calculator.py" in targets


def test_imports_in_returns_files_importing_this_one(ingested_sample):
    backend, project = ingested_sample
    results = tools.imports(backend, project, file="calculator.py",
                            direction="in")
    sources = {r["source_file"] for r in results}
    assert "runner.py" in sources


def test_graph_status_returns_expected_counts(ingested_sample):
    backend, project = ingested_sample
    status = tools.graph_status(backend, project)
    assert status["project"] == project
    assert status["files"] == 3
    assert status["classes"] == 1
    assert status["methods"] == 2
    assert status["calls_runtime_only"] >= 1
