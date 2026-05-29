"""Compare on-disk file hashes against stored Project content_hashes."""
from __future__ import annotations

import hashlib
import os

from livegraph.check.models import StalenessReport
from livegraph.discovery import discover_python_files
from livegraph.graph.backend import GraphBackend


def probe_staleness(
    root: str, backend: GraphBackend, project: str,
) -> StalenessReport:
    """Count files whose on-disk SHA-256 differs from the graph's
    stored content_hash, plus files present in one side only."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project,
    )
    stored: dict[str, str | None] = {r["path"]: r.get("hash") for r in rows}

    on_disk: dict[str, str] = {}
    for rel in discover_python_files(root):
        abs_path = os.path.join(root, rel)
        with open(abs_path, "rb") as h:
            on_disk[rel] = hashlib.sha256(h.read()).hexdigest()

    drifted = 0
    for rel, disk_hash in on_disk.items():
        if stored.get(rel) != disk_hash:
            drifted += 1
    for rel in stored.keys():
        if rel not in on_disk:
            drifted += 1
    return StalenessReport(drifted_files=drifted)
