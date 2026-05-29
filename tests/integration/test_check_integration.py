"""End-to-end: real Neo4j + .livegraph.toml + livegraph check."""
from __future__ import annotations

from pathlib import Path

import pytest

from livegraph.check.config import load_config
from livegraph.check.runner import compute_exit_code, run_checks
from livegraph.check.staleness import probe_staleness

pytestmark = pytest.mark.integration


@pytest.fixture()
def check_project(neo4j_backend, tmp_path):
    """Synthetic graph: 3 files with one import cycle and one layering
    violation. Also write a .livegraph.toml that catches them."""
    backend = neo4j_backend
    project = "check_test"

    backend.execute(
        "MERGE (p:Project {name: $project}) "
        "WITH p UNWIND $paths AS path "
        "MERGE (f:File {path: path, content_hash: 'h_' + path}) "
        "MERGE (p)-[:CONTAINS]->(f)",
        project=project,
        paths=["web/handlers.py", "domain/calc.py", "infra/db.py"],
    )
    # Import cycle web <-> domain
    backend.execute(
        "MATCH (a:File {path: 'web/handlers.py'}), "
        "      (b:File {path: 'domain/calc.py'}) "
        "MERGE (a)-[:IMPORTS]->(b) "
        "MERGE (b)-[:IMPORTS]->(a)",
    )

    cfg = tmp_path / ".livegraph.toml"
    cfg.write_text(f"""
[project]
name = "{project}"

[checks.cycles]
enabled = true
scope = "module"
max_cycles = 0

[checks.layering]
enabled = true
edge_kind = "imports"
max_violations = 0
layers = [
    {{ name = "web", patterns = ["web/**"] }},
    {{ name = "domain", patterns = ["domain/**"] }},
    {{ name = "infra", patterns = ["infra/**"] }},
]
""")
    # Create the files on disk so the staleness probe doesn't drown the
    # test in spurious "missing on disk" drift.
    for f in ["web/handlers.py", "domain/calc.py", "infra/db.py"]:
        target = tmp_path / f
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# placeholder\n")
    return backend, project, tmp_path, cfg


def test_full_run_reports_cycles_and_layering(check_project):
    backend, project, root, cfg_path = check_project
    cfg = load_config(cfg_path)
    staleness = probe_staleness(str(root), backend, project)
    report = run_checks(cfg, backend, staleness=staleness)
    by_check = {r.check: r for r in report.results}
    assert by_check["cycles"].status == "failed"
    assert by_check["layering"].status == "failed"
    code = compute_exit_code(report.results, staleness, strict=False)
    assert code == 1


def test_strict_promotes_staleness_to_exit_2(check_project):
    backend, project, root, cfg_path = check_project
    cfg = load_config(cfg_path)
    # Synthesize drift by writing to one file's content (the stored
    # hash is 'h_...' which won't match real SHA-256).
    (root / "web" / "handlers.py").write_text("def x(): pass\n")
    staleness = probe_staleness(str(root), backend, project)
    assert staleness.has_drift
    report = run_checks(cfg, backend, staleness=staleness)
    code = compute_exit_code(report.results, staleness, strict=True)
    assert code == 2
