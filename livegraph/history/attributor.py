"""Attribute commit hunks to current-source symbols by line overlap.

We look up symbols defined in the file in question, then compute the
overlap between each hunk and each symbol's `start_line..end_line`. The
attribution uses the CURRENT parse, so a symbol whose lines moved
across history may be over- or under-credited for past commits. That's
the documented trade-off; see the design spec.
"""
from __future__ import annotations

from collections.abc import Iterable

from livegraph.graph.backend import GraphBackend
from livegraph.history.models import HunkRange

_FILE_SYMBOLS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->"
    "(:File {path: $file})-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE s:Function OR s:Method "
    "RETURN s.qualified_name AS qualified_name, "
    "       s.start_line AS start_line, "
    "       s.end_line AS end_line"
)


def _overlap_lines(hunk: HunkRange, sym_start: int, sym_end: int) -> int:
    lo = max(hunk.start, sym_start)
    hi = min(hunk.end, sym_end)
    return max(0, hi - lo + 1)


def attribute_hunks(
    backend: GraphBackend,
    project: str,
    file_path: str,
    hunks: Iterable[HunkRange],
) -> dict[str, int]:
    """Return {qualified_name: total_overlapped_lines} for the file's
    symbols against the given hunks. Returns {} if no hunks or no
    overlap.
    """
    hunks = tuple(hunks)
    if not hunks:
        return {}
    rows = backend.execute(
        _FILE_SYMBOLS_CYPHER, project=project, file=file_path,
    )
    if not rows:
        return {}

    out: dict[str, int] = {}
    for row in rows:
        qn = row.get("qualified_name")
        s_start = row.get("start_line")
        s_end = row.get("end_line")
        if qn is None or s_start is None or s_end is None:
            continue
        total = 0
        for h in hunks:
            total += _overlap_lines(h, int(s_start), int(s_end))
        if total > 0:
            out[qn] = out.get(qn, 0) + total
    return out
