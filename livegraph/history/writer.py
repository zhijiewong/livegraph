"""Batched Cypher writes for the git-history layer."""
from __future__ import annotations

from collections.abc import Iterable

from livegraph.graph.backend import GraphBackend
from livegraph.history.models import CommitRecord


class HistoryWriter:
    """Writes Commit / Author / CHANGED_IN / AUTHORED in batched UNWIND.

    Each call to `write_commits` is idempotent thanks to MERGE-by-sha
    on Commit and MERGE-by-email on Author.
    """

    def __init__(self, backend: GraphBackend, batch_size: int = 500) -> None:
        self._backend = backend
        self._batch_size = batch_size

    def write_commits(
        self,
        project: str,
        commits: Iterable[CommitRecord],
        symbol_attributions: dict[str, dict[str, int]] | None = None,
    ) -> None:
        commits = list(commits)
        if not commits:
            return
        attrs = symbol_attributions or {}

        self._write_authors(commits)
        self._write_commits(project, commits)
        self._write_file_edges(commits)
        self._write_symbol_edges(commits, attrs)

    def _write_authors(self, commits: list[CommitRecord]) -> None:
        rows = [{"email": c.author_email, "name": c.author_name}
                for c in commits]
        self._batched(
            "UNWIND $rows AS row "
            "MERGE (a:Author {email: row.email}) "
            "SET a.name = coalesce(row.name, a.name)",
            rows,
        )

    def _write_commits(self, project: str, commits: list[CommitRecord]) -> None:
        rows = [{
            "sha": c.sha, "short_sha": c.short_sha, "message": c.message,
            "timestamp": c.timestamp, "email": c.author_email,
        } for c in commits]
        self._batched_with_project(
            project,
            "MERGE (p:Project {name: $project}) "
            "WITH p UNWIND $rows AS row "
            "MERGE (c:Commit {sha: row.sha}) "
            "SET c.short_sha = row.short_sha, c.message = row.message, "
            "    c.timestamp = row.timestamp, c.author_email = row.email "
            "MERGE (p)-[:CONTAINS]->(c) "
            "WITH c, row "
            "MATCH (a:Author {email: row.email}) "
            "MERGE (a)-[:AUTHORED]->(c)",
            rows,
        )

    def _write_file_edges(self, commits: list[CommitRecord]) -> None:
        rows = []
        for c in commits:
            for f in c.files:
                rows.append({
                    "sha": c.sha, "path": f.path,
                    "additions": f.additions, "deletions": f.deletions,
                })
        if not rows:
            return
        self._batched(
            "UNWIND $rows AS row "
            "MATCH (c:Commit {sha: row.sha}) "
            "MATCH (f:File {path: row.path}) "
            "MERGE (f)-[e:CHANGED_IN]->(c) "
            "SET e.additions = row.additions, e.deletions = row.deletions",
            rows,
        )

    def _write_symbol_edges(
        self, commits: list[CommitRecord],
        attrs: dict[str, dict[str, int]],
    ) -> None:
        rows = []
        for c in commits:
            for qn, lines in attrs.get(c.sha, {}).items():
                rows.append({
                    "sha": c.sha, "qualified_name": qn,
                    "lines_overlapped": lines,
                })
        if not rows:
            return
        self._batched(
            "UNWIND $rows AS row "
            "MATCH (c:Commit {sha: row.sha}) "
            "MATCH (s:Symbol {qualified_name: row.qualified_name}) "
            "MERGE (s)-[e:CHANGED_IN]->(c) "
            "SET e.lines_overlapped = row.lines_overlapped",
            rows,
        )

    def set_last_history_sha(self, project: str, sha: str) -> None:
        self._backend.execute(
            "MERGE (p:Project {name: $project}) "
            "SET p.last_history_sha = $sha",
            project=project, sha=sha,
        )

    def _batched(self, cypher: str, rows: list[dict]) -> None:
        for start in range(0, len(rows), self._batch_size):
            self._backend.execute(cypher,
                                  rows=rows[start:start + self._batch_size])

    def _batched_with_project(
        self, project: str, cypher: str, rows: list[dict],
    ) -> None:
        for start in range(0, len(rows), self._batch_size):
            self._backend.execute(
                cypher, project=project,
                rows=rows[start:start + self._batch_size],
            )
