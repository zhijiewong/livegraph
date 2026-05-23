"""Incremental graph updates: detect file changes and re-ingest only those.

`detect_changes` walks the filesystem, computes SHA-256 of every .py file,
and compares against the `content_hash` stored on each `File` node.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from livegraph.discovery import discover_python_files
from livegraph.graph.backend import GraphBackend


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Classification of every project file vs. the stored graph state."""

    added: list[str]
    changed: list[str]
    deleted: list[str]
    unchanged: list[str]
    hashes: dict[str, str] = field(default_factory=dict)


def detect_changes(root: str, backend: GraphBackend,
                   project: str) -> ChangeSet:
    """Classify every file in ``root`` vs. the graph's stored state."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project,
    )
    stored = {row["path"]: row.get("hash") for row in rows}

    on_disk: dict[str, str] = {}
    for rel in discover_python_files(root):
        abs_path = os.path.join(root, rel)
        with open(abs_path, "rb") as handle:
            on_disk[rel] = hashlib.sha256(handle.read()).hexdigest()

    stored_set = set(stored)
    disk_set = set(on_disk)
    added = sorted(disk_set - stored_set)
    deleted = sorted(stored_set - disk_set)
    intersect = disk_set & stored_set
    changed = sorted(p for p in intersect if stored.get(p) != on_disk[p])
    unchanged = sorted(p for p in intersect if stored.get(p) == on_disk[p])

    return ChangeSet(
        added=added, changed=changed, deleted=deleted,
        unchanged=unchanged, hashes=on_disk,
    )
