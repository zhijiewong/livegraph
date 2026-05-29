"""MCP tools that read the git-history layer (Phase 10).

Three tools: ``symbol_history``, ``recent_changes``, ``top_churn``.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend

_VALID_KINDS = ("any", "function", "method")
_MAX_LIMIT = 100
_MAX_WINDOW_DAYS = 3650  # ~10 years

_HISTORY_INGESTED_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:Commit) "
    "RETURN count(*) AS n LIMIT 1"
)


def _history_present(backend: GraphBackend, project: str) -> bool:
    rows = backend.execute(_HISTORY_INGESTED_CYPHER, project=project)
    if not rows:
        return False
    return int(rows[0].get("n") or 0) > 0


# -- symbol_history ----------------------------------------------------

_SYMBOL_HISTORY_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(c:Commit) "
    "MATCH (s {qualified_name: $qualified_name})-[e:CHANGED_IN]->(c) "
    "WHERE s:Function OR s:Method "
    "OPTIONAL MATCH (a:Author)-[:AUTHORED]->(c) "
    "RETURN c.sha AS sha, c.short_sha AS short_sha, "
    "       c.message AS message, c.timestamp AS timestamp, "
    "       c.author_email AS author_email, a.name AS author_name, "
    "       e.lines_overlapped AS lines_overlapped "
    "ORDER BY c.timestamp DESC "
    "LIMIT $limit"
)

_SYMBOL_HISTORY_COUNT = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(c:Commit) "
    "MATCH (s {qualified_name: $qualified_name})-[:CHANGED_IN]->(c) "
    "WHERE s:Function OR s:Method "
    "RETURN count(*) AS n"
)


def symbol_history(
    backend: GraphBackend, project: str, qualified_name: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Recent commits touching the symbol, newest first."""
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = backend.execute(
        _SYMBOL_HISTORY_CYPHER,
        project=project, qualified_name=qualified_name, limit=limit,
    )
    count_rows = backend.execute(
        _SYMBOL_HISTORY_COUNT,
        project=project, qualified_name=qualified_name,
    )
    total = int(count_rows[0].get("n") or 0) if count_rows else 0
    warning = None
    if not rows and not _history_present(backend, project):
        warning = (
            "no git history ingested; run `livegraph ingest-history`"
        )
    return {
        "qualified_name": qualified_name,
        "commits": rows,
        "total_commits": total,
        "warning": warning,
    }


# -- recent_changes ----------------------------------------------------

_RECENT_CHANGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(c:Commit) "
    "WHERE ($since IS NULL OR c.timestamp >= $since) "
    "MATCH (s)-[:CHANGED_IN]->(c) "
    "WHERE ($kind = 'any' AND (s:Function OR s:Method)) "
    "   OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "   OR ($kind = 'method' AND s:Method) "
    "WITH s, max(c.timestamp) AS last_changed, "
    "     count(DISTINCT c) AS commit_count "
    "MATCH (s)-[:CHANGED_IN]->(lc:Commit {timestamp: last_changed}) "
    "WITH s, last_changed, commit_count, "
    "     collect(lc.sha)[0] AS latest_sha "
    "RETURN s.qualified_name AS qualified_name, "
    "       head([l IN labels(s) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       s.file AS file, "
    "       last_changed, commit_count, latest_sha "
    "ORDER BY last_changed DESC "
    "LIMIT $limit"
)


def recent_changes(
    backend: GraphBackend, project: str,
    since: str | None = None, limit: int = 50, kind: str = "any",
) -> dict[str, Any]:
    """Symbols changed in commits with timestamp >= since."""
    if kind not in _VALID_KINDS:
        return {
            "results": [],
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of 'any', 'function', 'method'"
            ),
        }
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = backend.execute(
        _RECENT_CHANGES_CYPHER,
        project=project, since=since, kind=kind, limit=limit,
    )
    warning = None
    if not rows and not _history_present(backend, project):
        warning = "no git history ingested; run `livegraph ingest-history`"
    return {"results": rows, "warning": warning}


# -- top_churn ---------------------------------------------------------

_TOP_CHURN_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(c:Commit) "
    "WHERE c.timestamp >= $cutoff "
    "MATCH (s)-[:CHANGED_IN]->(c) "
    "WHERE ($kind = 'any' AND (s:Function OR s:Method)) "
    "   OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "   OR ($kind = 'method' AND s:Method) "
    "OPTIONAL MATCH (a:Author)-[:AUTHORED]->(c) "
    "WITH s, "
    "     count(DISTINCT c) AS commit_count, "
    "     count(DISTINCT a) AS unique_authors, "
    "     min(c.timestamp) AS first_changed, "
    "     max(c.timestamp) AS last_changed "
    "RETURN s.qualified_name AS qualified_name, "
    "       head([l IN labels(s) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       s.file AS file, "
    "       commit_count, unique_authors, first_changed, last_changed "
    "ORDER BY commit_count DESC, last_changed DESC "
    "LIMIT $limit"
)


def top_churn(
    backend: GraphBackend, project: str,
    window_days: int = 30, limit: int = 20, kind: str = "any",
) -> dict[str, Any]:
    """Top-K symbols by distinct commits in the window."""
    if kind not in _VALID_KINDS:
        return {
            "window_days": window_days, "results": [],
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of 'any', 'function', 'method'"
            ),
        }
    window_days = max(1, min(int(window_days), _MAX_WINDOW_DAYS))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    cutoff_iso = _cutoff_iso(window_days)
    rows = backend.execute(
        _TOP_CHURN_CYPHER,
        project=project, cutoff=cutoff_iso, kind=kind, limit=limit,
    )
    warning = None
    if not rows and not _history_present(backend, project):
        warning = "no git history ingested; run `livegraph ingest-history`"
    return {"window_days": window_days, "results": rows, "warning": warning}


def _cutoff_iso(window_days: int) -> str:
    import datetime
    cutoff = datetime.datetime.now(datetime.timezone.utc) - \
        datetime.timedelta(days=window_days)
    return cutoff.isoformat()
