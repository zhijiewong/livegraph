"""End-to-end: real git repo + real Neo4j + history tools."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _git(repo: Path, *args, author=("Alice", "alice@x")):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": author[0], "GIT_COMMITTER_EMAIL": author[1],
        "GIT_AUTHOR_DATE": "2026-05-01T00:00:00",
        "GIT_COMMITTER_DATE": "2026-05-01T00:00:00",
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.fixture()
def history_project(neo4j_backend, tmp_path):
    """Build a tiny git repo, ingest it (Phase 1) + ingest its history."""
    from livegraph.history.ingest import ingest_history
    from livegraph.ingest import ingest_project

    backend = neo4j_backend
    project = "history_test"

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "alice@x")
    _git(tmp_path, "config", "user.name", "Alice")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "a.py").write_text(
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    )
    _git(tmp_path, "add", "pkg")
    _git(tmp_path, "commit", "-q", "-m", "add foo + bar")

    (tmp_path / "pkg" / "a.py").write_text(
        "def foo():\n    return 42\n\ndef bar():\n    return 2\n"
    )
    _git(tmp_path, "add", "pkg/a.py")
    _git(tmp_path, "commit", "-q", "-m", "tweak foo",
         author=("Bob", "bob@x"))

    ingest_project(str(tmp_path), backend, project_name=project)
    summary = ingest_history(str(tmp_path), backend, project)
    assert summary.commits == 2
    return backend, project


def test_symbol_history_returns_commits_for_foo(history_project):
    from livegraph.mcp.tools_history import symbol_history

    backend, project = history_project
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->"
        "(:File {path: 'pkg/a.py'})-[:DEFINES]->(s:Function {name: 'foo'}) "
        "RETURN s.qualified_name AS qn",
        project=project,
    )
    foo_qn = rows[0]["qn"]

    result = symbol_history(backend, project, qualified_name=foo_qn, limit=10)
    assert result["warning"] is None
    # foo was touched in both commits.
    assert result["total_commits"] >= 2
    messages = [c["message"] for c in result["commits"]]
    assert any("tweak foo" in m for m in messages)


def test_recent_changes_lists_foo(history_project):
    from livegraph.mcp.tools_history import recent_changes

    backend, project = history_project
    result = recent_changes(backend, project, limit=50)
    qns = [r["qualified_name"] for r in result["results"]]
    # Qualified names use '::' separator, e.g. 'pkg/a.py::foo'
    assert any("foo" in q for q in qns)


def test_top_churn_ranks_foo_above_bar(history_project):
    from livegraph.mcp.tools_history import top_churn

    backend, project = history_project
    result = top_churn(backend, project, window_days=3650, limit=10)
    # foo was changed in 2 commits, bar in 1. foo should rank first.
    # Qualified names use '::' separator, e.g. 'pkg/a.py::foo'
    qns_in_order = [r["qualified_name"] for r in result["results"]]
    foo_idx = next(i for i, q in enumerate(qns_in_order) if "foo" in q)
    bar_idxs = [i for i, q in enumerate(qns_in_order) if "bar" in q]
    if bar_idxs:
        assert foo_idx < bar_idxs[0]
