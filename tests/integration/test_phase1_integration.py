"""Phase 1 end-to-end against a real Neo4j."""
import pytest

from livegraph.ingest import ingest_project

pytestmark = pytest.mark.integration


def test_ingest_sample_project_writes_expected_nodes(
    neo4j_backend, sample_project_path,
):
    summary = ingest_project(sample_project_path, neo4j_backend,
                             project_name="sample", batch_size=100)
    assert summary.files == 3
    assert summary.parse_errors == 0

    rows = neo4j_backend.execute(
        "MATCH (m:Method {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN m.name AS name"
    )
    assert rows == [{"name": "add"}]

    classes = neo4j_backend.execute(
        "MATCH (:Class {qualified_name: 'calculator.py::Calculator'})"
        "-[:HAS_METHOD]->(m:Method) RETURN count(m) AS n"
    )
    assert classes[0]["n"] == 2


def test_ingest_is_idempotent(neo4j_backend, sample_project_path):
    for _ in range(2):
        ingest_project(sample_project_path, neo4j_backend,
                       project_name="sample", batch_size=100)
    rows = neo4j_backend.execute("MATCH (f:File) RETURN count(f) AS n")
    assert rows[0]["n"] == 3
