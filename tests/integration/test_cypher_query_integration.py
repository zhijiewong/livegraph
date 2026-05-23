"""End-to-end tests for describe_schema and run_cypher against real Neo4j."""
import pytest

from livegraph.mcp.cypher_guard import CypherTimeoutError
from livegraph.mcp import tools

pytestmark = pytest.mark.integration


def test_describe_schema_roundtrip_against_real_graph(ingested_sample):
    """Every node label in describe_schema must appear in the actual graph."""
    backend, project = ingested_sample
    schema = tools.describe_schema(backend, project)
    declared_labels = set(schema["node_labels"]) - {"Test"}
    # Test is a secondary label; it may or may not appear via :Function nodes
    # that happen to also be tests. We assert the primary labels only.

    actual_rows = backend.execute(
        "CALL db.labels() YIELD label RETURN collect(label) AS labels"
    )
    actual_labels = set(actual_rows[0]["labels"]) if actual_rows else set()
    assert declared_labels <= actual_labels


def test_run_cypher_dynamic_dispatch_example_finds_differentiator(
    ingested_sample,
):
    """Phase 6 acceptance test.

    Running the dynamic-dispatch example query verbatim must surface
    the runner.py::run_operation -> calculator.py::Calculator.add edge
    that no static-only code-graph tool can produce.
    """
    backend, project = ingested_sample
    schema = tools.describe_schema(backend, project)
    dyn_example = next(
        ex for ex in schema["example_queries"]
        if "Dynamic-dispatch" in ex["intent"]
    )
    result = tools.run_cypher(backend, project, query=dyn_example["query"])
    pairs = {
        (row["caller.qualified_name"], row["callee.qualified_name"])
        for row in result["rows"]
    }
    assert ("runner.py::run_operation",
            "calculator.py::Calculator.add") in pairs


def test_run_cypher_read_transaction_blocks_write(ingested_sample):
    """A write query that bypasses the lexer must be rejected at the engine."""
    backend, project = ingested_sample
    raised = False
    try:
        backend.execute_read("CREATE (n:_PhaseSixProbe) RETURN n",
                             timeout_seconds=10)
    except Exception as exc:
        raised = True
        assert ("write" in str(exc).lower()
                or "access" in str(exc).lower()
                or "read" in str(exc).lower()
                or "ForbiddenAction" in type(exc).__name__)
    assert raised, "engine should have refused the write inside a read tx"

    rows = backend.execute(
        "MATCH (n:_PhaseSixProbe) RETURN count(n) AS n",
    )
    assert rows[0]["n"] == 0


def test_run_cypher_timeout_fires(ingested_sample):
    """A CPU-bound query against a tight timeout must raise CypherTimeoutError."""
    backend, project = ingested_sample
    with pytest.raises(CypherTimeoutError):
        # Large UNWIND range creates enough CPU work to exceed a 1-second
        # transaction timeout even on fast hardware.
        tools.run_cypher(
            backend, project,
            query=(
                "UNWIND range(1, 100000000) AS x "
                "UNWIND range(1, 100) AS y "
                "WITH x, y WHERE x + y > 200000000 "
                "RETURN count(*) AS n"
            ),
            timeout_seconds=1,
        )


def test_run_cypher_truncated_flag_set(ingested_sample):
    """A query that returns many rows must surface the truncated flag."""
    backend, project = ingested_sample
    result = tools.run_cypher(
        backend, project,
        query="MATCH (n) RETURN n LIMIT 100",
        row_limit=3,
    )
    assert result["truncated"] is True
    assert result["row_count"] == 3


def test_run_cypher_project_auto_injected_against_real_graph(
    ingested_sample,
):
    """A query referencing $project without passing it must scope correctly."""
    backend, project = ingested_sample
    result = tools.run_cypher(
        backend, project,
        query=("MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
               "RETURN f.path AS path"),
    )
    paths = {row["path"] for row in result["rows"]}
    assert paths == {"calculator.py", "runner.py", "test_calculator.py"}
