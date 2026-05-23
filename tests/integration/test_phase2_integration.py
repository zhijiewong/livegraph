"""Phase 2 end-to-end: the differentiator test.

Proves that runtime tracing catches a dynamic-dispatch call that static
analysis cannot resolve.
"""
import sys

import pytest

from livegraph.augment import augment_from_observations
from livegraph.ingest import ingest_project
from livegraph.runtime.runner import run_pytest

pytestmark = pytest.mark.integration


def test_runtime_catches_dynamic_dispatch(neo4j_backend, sample_project_path):
    ingest_project(sample_project_path, neo4j_backend,
                   project_name="sample", batch_size=100)

    # The static call resolver must NOT have linked run_operation -> add,
    # because `op(a, b)` is dynamic dispatch.
    static_edge = neo4j_backend.execute(
        "MATCH (:Function {qualified_name: 'runner.py::run_operation'})"
        "-[c:CALLS]->(:Method {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c.static AS static"
    )
    assert static_edge == []

    observations = run_pytest(sample_project_path, python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)

    rows = neo4j_backend.execute(
        "MATCH (:Function {qualified_name: 'runner.py::run_operation'})"
        "-[c:CALLS]->(:Method {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c.static AS static, c.runtime AS runtime"
    )
    assert len(rows) == 1, "runtime tracing should have found the call"
    assert rows[0]["runtime"] is True
    assert rows[0]["static"] in (False, None)


def test_phase2_writes_test_nodes_and_coverage(
    neo4j_backend, sample_project_path,
):
    ingest_project(sample_project_path, neo4j_backend,
                   project_name="sample", batch_size=100)
    observations = run_pytest(sample_project_path, python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)

    tests = neo4j_backend.execute(
        "MATCH (t:Test) RETURN count(t) AS n")
    assert tests[0]["n"] >= 3

    covers = neo4j_backend.execute(
        "MATCH (:Test)-[c:COVERS]->() RETURN count(c) AS n")
    assert covers[0]["n"] >= 1
