"""Orchestrate the git-history ingest pipeline."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from livegraph.graph.backend import GraphBackend
from livegraph.history.attributor import attribute_hunks
from livegraph.history.extractor import iter_commits
from livegraph.history.writer import HistoryWriter

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestHistorySummary:
    commits: int
    files: int
    symbol_attributions: int


def _read_last_history_sha(backend: GraphBackend, project: str) -> str | None:
    rows = backend.execute(
        "MATCH (p:Project {name: $project}) "
        "RETURN p.last_history_sha AS sha",
        project=project,
    )
    if not rows:
        return None
    sha = rows[0].get("sha")
    return sha or None


def ingest_history(
    root: str,
    backend: GraphBackend,
    project: str,
    since_last: bool = False,
    max_commits: int | None = None,
    batch_size: int = 500,
) -> IngestHistorySummary:
    """Walk git history at `root` and write commit/author/CHANGED_IN
    edges to the project's graph.
    """
    since = None
    if since_last:
        since = _read_last_history_sha(backend, project)
        if since is None:
            log.info("first history ingest for project %r", project)

    commits = list(iter_commits(root, since=since, max_commits=max_commits))
    if not commits:
        return IngestHistorySummary(commits=0, files=0, symbol_attributions=0)

    attributions: dict[str, dict[str, int]] = {}
    n_files = 0
    n_sym_edges = 0
    for c in commits:
        per_commit: dict[str, int] = {}
        for f in c.files:
            n_files += 1
            attributed = attribute_hunks(
                backend, project=project, file_path=f.path, hunks=f.hunks,
            )
            for qn, lines in attributed.items():
                per_commit[qn] = per_commit.get(qn, 0) + lines
        if per_commit:
            attributions[c.sha] = per_commit
            n_sym_edges += len(per_commit)

    writer = HistoryWriter(backend, batch_size=batch_size)
    writer.write_commits(project, commits, symbol_attributions=attributions)

    newest = commits[0].sha  # iter_commits returns newest-first
    writer.set_last_history_sha(project, newest)

    return IngestHistorySummary(
        commits=len(commits), files=n_files,
        symbol_attributions=n_sym_edges,
    )
