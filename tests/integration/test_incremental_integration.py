"""End-to-end tests for incremental updates against a real Neo4j."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from livegraph.augment import augment_from_observations
from livegraph.incremental import detect_changes, reingest_files
from livegraph.ingest import ingest_project
from livegraph.runtime.runner import run_pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def mutable_sample(tmp_path, sample_project_path, neo4j_backend):
    """Copy the sample project to a writable tmp dir and ingest it."""
    root = tmp_path / "sample"
    shutil.copytree(sample_project_path, root)
    ingest_project(str(root), neo4j_backend, project_name="sample",
                   batch_size=100)
    observations = run_pytest(str(root), python=sys.executable)
    augment_from_observations(observations, neo4j_backend, batch_size=100)
    yield neo4j_backend, "sample", str(root)


def test_update_no_op_when_nothing_changed(mutable_sample):
    backend, project, root = mutable_sample
    cs = detect_changes(root, backend, project)
    assert cs.changed == [] and cs.added == [] and cs.deleted == []
    assert set(cs.unchanged) == {"calculator.py", "runner.py",
                                 "test_calculator.py"}
    summary = reingest_files(root, backend, project, cs, batch_size=100)
    assert summary.added == 0
    assert summary.changed == 0
    assert summary.deleted == 0
    assert summary.unchanged == 3
    assert summary.parse_errors == 0


def test_update_adds_new_function_to_runner(mutable_sample):
    backend, project, root = mutable_sample
    runner_py = Path(root) / "runner.py"
    runner_py.write_text(
        runner_py.read_text()
        + "\n\ndef brand_new():\n    return 42\n"
    )
    cs = detect_changes(root, backend, project)
    assert cs.changed == ["runner.py"]
    reingest_files(root, backend, project, cs, batch_size=100)

    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File "
        "    {path: 'runner.py'})-[:DEFINES]->(f:Function "
        "    {qualified_name: 'runner.py::brand_new'}) "
        "RETURN f.name AS name",
        project=project,
    )
    assert rows == [{"name": "brand_new"}]

    # The differentiator edge from Phase 2 must survive the update.
    edge = backend.execute(
        "MATCH (:Function {qualified_name: 'runner.py::run_operation'})"
        "-[c:CALLS]->(:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c.runtime AS runtime",
    )
    assert edge == [{"runtime": True}]


def test_update_removes_a_method(mutable_sample):
    backend, project, root = mutable_sample
    calc = Path(root) / "calculator.py"
    new_src = calc.read_text().replace(
        "    def multiply(self, a, b):\n        return a * b\n", "",
    )
    calc.write_text(new_src)

    cs = detect_changes(root, backend, project)
    assert cs.changed == ["calculator.py"]
    reingest_files(root, backend, project, cs, batch_size=100)

    rows = backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.multiply'}) "
        "RETURN m.name AS name",
    )
    assert rows == []


def test_update_handles_deleted_file(mutable_sample):
    backend, project, root = mutable_sample
    (Path(root) / "runner.py").unlink()

    cs = detect_changes(root, backend, project)
    assert cs.deleted == ["runner.py"]
    reingest_files(root, backend, project, cs, batch_size=100)

    files = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File "
        "    {path: 'runner.py'}) RETURN f.path AS path",
        project=project,
    )
    assert files == []

    edge = backend.execute(
        "MATCH (a {qualified_name: 'runner.py::run_operation'})-[c:CALLS]->"
        "(b {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN c",
    )
    assert edge == []


def test_runtime_stale_lifecycle(mutable_sample):
    backend, project, root = mutable_sample
    calc = Path(root) / "calculator.py"
    calc.write_text(calc.read_text() + "\n# trailing comment\n")
    cs = detect_changes(root, backend, project)
    reingest_files(root, backend, project, cs, batch_size=100)

    rows = backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN coalesce(m.runtime_stale, false) AS stale",
    )
    assert rows == [{"stale": True}]

    observations = run_pytest(root, python=sys.executable)
    augment_from_observations(observations, backend, batch_size=100)

    rows = backend.execute(
        "MATCH (m:Method "
        "    {qualified_name: 'calculator.py::Calculator.add'}) "
        "RETURN coalesce(m.runtime_stale, false) AS stale",
    )
    assert rows == [{"stale": False}]


def test_dry_run_does_not_modify_graph(mutable_sample):
    backend, project, root = mutable_sample
    calc = Path(root) / "calculator.py"
    calc.write_text(calc.read_text() + "\n# touched\n")

    before = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File "
        "    {path: 'calculator.py'}) RETURN f.content_hash AS h",
        project=project,
    )

    cs = detect_changes(root, backend, project)
    assert cs.changed == ["calculator.py"]
    # Dry-run: do NOT call reingest_files. Verify the graph is unchanged.
    after = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File "
        "    {path: 'calculator.py'}) RETURN f.content_hash AS h",
        project=project,
    )
    assert before == after
