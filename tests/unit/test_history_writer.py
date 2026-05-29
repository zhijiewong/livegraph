from __future__ import annotations

from typing import Any

from livegraph.history.models import CommitRecord, FileChange
from livegraph.history.writer import HistoryWriter


class _RecordingBackend:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        return []

    def verify(self): return None
    def close(self): return None


def _commit(sha: str = "a" * 40, files=()) -> CommitRecord:
    return CommitRecord(
        sha=sha, short_sha=sha[:7], message="m",
        timestamp="2026-01-01T00:00:00+00:00",
        author_email="alice@x", author_name="Alice", files=tuple(files),
    )


def test_writer_writes_project_commit_and_author():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.write_commits(project="p", commits=[
        _commit(files=(FileChange(path="a.py", additions=1, deletions=0),)),
    ])
    cyphers = " || ".join(c[0] for c in backend.calls)
    assert "MERGE (a:Author" in cyphers
    assert "MERGE (c:Commit" in cyphers
    assert "MERGE (a)-[:AUTHORED]->(c)" in cyphers
    assert "MERGE (p)-[:CONTAINS]->(c)" in cyphers


def test_writer_writes_file_changed_in_edge():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.write_commits(project="p", commits=[
        _commit(files=(
            FileChange(path="a.py", additions=3, deletions=2),
            FileChange(path="b.py", additions=0, deletions=4),
        )),
    ])
    file_call = next(
        c for c in backend.calls if "(f:File" in c[0] and "CHANGED_IN" in c[0]
    )
    rows = file_call[1]["rows"]
    paths = {r["path"] for r in rows}
    assert paths == {"a.py", "b.py"}


def test_writer_writes_symbol_changed_in_when_attribution_present():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.write_commits(project="p", commits=[
        _commit(files=()),
    ], symbol_attributions={
        "a" * 40: {"pkg.foo": 3, "pkg.bar": 7},
    })
    sym_call = next(
        c for c in backend.calls if "(s:Symbol" in c[0] and "CHANGED_IN" in c[0]
    )
    rows = sym_call[1]["rows"]
    by_qn = {r["qualified_name"]: r["lines_overlapped"] for r in rows}
    assert by_qn == {"pkg.foo": 3, "pkg.bar": 7}


def test_writer_updates_last_history_sha():
    backend = _RecordingBackend()
    w = HistoryWriter(backend, batch_size=10)
    w.set_last_history_sha("p", "deadbeef")
    last_call = backend.calls[-1]
    assert "Project" in last_call[0]
    assert last_call[1] == {"project": "p", "sha": "deadbeef"}
