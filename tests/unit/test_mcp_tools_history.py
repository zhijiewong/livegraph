from __future__ import annotations

from typing import Any

from livegraph.mcp.tools_history import (
    recent_changes, symbol_history, top_churn,
)


class _FakeBackend:
    def __init__(self, responses):
        # responses: dict of substring-of-cypher → rows
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, cypher, **params):
        self.calls.append((cypher, params))
        for key, rows in self._responses.items():
            if key in cypher:
                return rows
        return []

    def verify(self): return None
    def close(self): return None


# ---- symbol_history --------------------------------------------------

def test_symbol_history_returns_commits_newest_first():
    backend = _FakeBackend({
        "MATCH (s {qualified_name": [
            {"sha": "b" * 40, "short_sha": "bbbbbbb", "message": "newer",
             "timestamp": "2026-05-15T00:00:00+00:00",
             "author_email": "alice@x", "author_name": "Alice",
             "lines_overlapped": 5},
            {"sha": "a" * 40, "short_sha": "aaaaaaa", "message": "older",
             "timestamp": "2026-04-15T00:00:00+00:00",
             "author_email": "alice@x", "author_name": "Alice",
             "lines_overlapped": 2},
        ],
        "RETURN count": [{"n": 2}],
    })
    result = symbol_history(backend, project="p",
                            qualified_name="pkg.foo", limit=10)
    assert result["warning"] is None
    shas = [c["sha"] for c in result["commits"]]
    assert shas == ["b" * 40, "a" * 40]
    assert result["total_commits"] == 2


def test_symbol_history_warns_when_no_history_ingested():
    backend = _FakeBackend({
        "MATCH (s {qualified_name": [],
        "RETURN count": [{"n": 0}],
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:Commit)": [
            {"n": 0},
        ],
    })
    result = symbol_history(backend, project="p", qualified_name="pkg.foo")
    assert result["commits"] == []
    assert "ingest-history" in (result["warning"] or "")


# ---- recent_changes --------------------------------------------------

def test_recent_changes_returns_symbols_ordered_by_last_changed():
    backend = _FakeBackend({
        "ORDER BY last_changed DESC": [
            {"qualified_name": "pkg.foo", "kind": "function",
             "file": "pkg/a.py",
             "last_changed": "2026-05-29T00:00:00+00:00",
             "commit_count": 3, "latest_sha": "abc"},
            {"qualified_name": "pkg.bar", "kind": "method",
             "file": "pkg/b.py",
             "last_changed": "2026-05-20T00:00:00+00:00",
             "commit_count": 1, "latest_sha": "def"},
        ],
    })
    result = recent_changes(backend, project="p", limit=50)
    qns = [r["qualified_name"] for r in result["results"]]
    assert qns == ["pkg.foo", "pkg.bar"]


def test_recent_changes_clamps_limit_to_100():
    backend = _FakeBackend({})
    recent_changes(backend, project="p", limit=9999)
    cyphers = [c for c in backend.calls if "$limit" in c[0]]
    assert cyphers and cyphers[0][1]["limit"] == 100


def test_recent_changes_kind_filter_validated():
    backend = _FakeBackend({})
    result = recent_changes(backend, project="p", kind="garbage")
    assert result["results"] == []
    assert "kind" in (result["warning"] or "").lower()


# ---- top_churn -------------------------------------------------------

def test_top_churn_returns_ranked_results():
    backend = _FakeBackend({
        "ORDER BY commit_count DESC": [
            {"qualified_name": "pkg.foo", "kind": "function",
             "file": "pkg/a.py", "commit_count": 14,
             "unique_authors": 3,
             "first_changed": "2026-05-01T00:00:00+00:00",
             "last_changed": "2026-05-29T00:00:00+00:00"},
        ],
    })
    result = top_churn(backend, project="p", window_days=30)
    assert result["window_days"] == 30
    assert result["results"][0]["commit_count"] == 14
    assert result["results"][0]["unique_authors"] == 3


def test_top_churn_clamps_window_days_to_3650():
    backend = _FakeBackend({})
    result = top_churn(backend, project="p", window_days=99999)
    assert result["window_days"] == 3650
