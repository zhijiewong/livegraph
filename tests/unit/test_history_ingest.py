from __future__ import annotations

from typing import Any

from livegraph.history.ingest import IngestHistorySummary, ingest_history
from livegraph.history.models import CommitRecord, FileChange, HunkRange


class _Backend:
    def __init__(self, stored_sha=None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._stored = stored_sha

    def execute(self, cypher: str, **params):
        self.calls.append((cypher, params))
        if "last_history_sha" in cypher and "RETURN" in cypher:
            return [{"sha": self._stored}]
        return []

    def verify(self): return None
    def close(self): return None


def _commit(sha):
    return CommitRecord(
        sha=sha, short_sha=sha[:7], message="m",
        timestamp="2026-01-01T00:00:00+00:00",
        author_email="a@x", author_name="A",
        files=(FileChange(path="a.py", additions=1, deletions=0,
                          hunks=(HunkRange(1, 2),)),),
    )


def test_ingest_writes_commits_and_returns_summary(monkeypatch, tmp_path):
    backend = _Backend()
    commits = [_commit("a" * 40), _commit("b" * 40)]
    monkeypatch.setattr(
        "livegraph.history.ingest.iter_commits",
        lambda *a, **kw: iter(commits),
    )
    monkeypatch.setattr(
        "livegraph.history.ingest.attribute_hunks",
        lambda backend, project, file_path, hunks: {"pkg.foo": 2},
    )
    summary = ingest_history(str(tmp_path), backend, project="p")
    assert isinstance(summary, IngestHistorySummary)
    assert summary.commits == 2
    assert summary.symbol_attributions == 2  # one per commit
    # last_history_sha was set to the HEAD (newest) commit.
    last_set = [c for c in backend.calls
                if "last_history_sha" in c[0] and "SET" in c[0]]
    assert last_set
    assert last_set[-1][1]["sha"] == "a" * 40  # newest first


def test_since_last_reads_stored_sha_and_passes_to_iter(monkeypatch, tmp_path):
    backend = _Backend(stored_sha="cafef00d")
    captured = {}

    def fake_iter(root, since=None, max_commits=None):
        captured["since"] = since
        return iter([])

    monkeypatch.setattr(
        "livegraph.history.ingest.iter_commits", fake_iter,
    )
    ingest_history(str(tmp_path), backend, project="p", since_last=True)
    assert captured["since"] == "cafef00d"


def test_since_last_with_no_stored_sha_falls_back_to_full(
    monkeypatch, tmp_path,
):
    backend = _Backend(stored_sha=None)
    captured = {}

    def fake_iter(root, since=None, max_commits=None):
        captured["since"] = since
        return iter([])

    monkeypatch.setattr(
        "livegraph.history.ingest.iter_commits", fake_iter,
    )
    ingest_history(str(tmp_path), backend, project="p", since_last=True)
    assert captured["since"] is None
