"""End-to-end: real Neo4j + the sample TS project."""
from __future__ import annotations

import os

import pytest

from livegraph.ingest import ingest_project

pytestmark = pytest.mark.integration

SAMPLE_TS = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "sample_project_ts",
)


@pytest.fixture()
def ts_project(neo4j_backend):
    root = os.path.abspath(SAMPLE_TS)
    summary = ingest_project(root, neo4j_backend, project_name="sample_ts")
    assert summary.files >= 4
    return neo4j_backend, "sample_ts"


def test_calculator_class_and_methods_ingested(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->"
        "(:File {path: 'src/calc.ts'})-[:DEFINES]->(c:Class) "
        "RETURN c.qualified_name AS qn",
        project=project,
    )
    qns = {r["qn"] for r in rows}
    assert "src/calc.ts::Calculator" in qns


def test_methods_attached_to_class(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:Class {qualified_name: 'src/calc.ts::Calculator'})"
        "-[:HAS_METHOD]->(m:Method) "
        "RETURN m.qualified_name AS qn",
    )
    qns = {r["qn"] for r in rows}
    assert "src/calc.ts::Calculator.add" in qns
    assert "src/calc.ts::Calculator.multiply" in qns


def test_tsconfig_alias_resolves_to_file_import(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:File {path: 'src/index.ts'})-[:IMPORTS]->(f:File) "
        "RETURN f.path AS path",
    )
    paths = {r["path"] for r in rows}
    assert "src/util.ts" in paths  # via @/util alias
    assert "src/calc.ts" in paths  # via relative ./calc


def test_index_calls_calculator_add(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (caller {qualified_name: 'src/index.ts::default'})"
        "-[:CALLS]->(callee) "
        "WHERE callee.qualified_name = 'src/calc.ts::Calculator.add' "
        "RETURN count(*) AS n",
    )
    assert rows[0]["n"] >= 1


def test_default_export_named_default(ts_project):
    backend, project = ts_project
    rows = backend.execute(
        "MATCH (:File {path: 'src/index.ts'})-[:DEFINES]->(f) "
        "WHERE f.qualified_name = 'src/index.ts::default' "
        "RETURN f.qualified_name AS qn",
    )
    assert rows[0]["qn"] == "src/index.ts::default"
