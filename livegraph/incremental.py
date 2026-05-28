"""Incremental graph updates: detect file changes and re-ingest only those.

`detect_changes` walks the filesystem, computes SHA-256 of every .py file,
and compares against the `content_hash` stored on each `File` node.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field

from livegraph.discovery import discover_python_files, module_name
from livegraph.graph.backend import GraphBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord
from livegraph.static.extractor import extract
from livegraph.static.parser import has_errors, parse_source
from livegraph.static.resolver import resolve_calls, resolve_imports


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


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpdateSummary:
    """Counts produced by a `reingest_files` run."""

    added: int
    changed: int
    deleted: int
    unchanged: int
    parse_errors: int



def _read_project_defined(backend: GraphBackend, project: str) -> set[str]:
    """Return every Function/Method/Class qualified_name in the project."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
        "-[:DEFINES|HAS_METHOD*1..2]->(s) "
        "WHERE s:Function OR s:Method OR s:Class "
        "RETURN DISTINCT s.qualified_name AS qualified_name",
        project=project,
    )
    return {row["qualified_name"] for row in rows}


def _read_project_modules(backend: GraphBackend,
                          project: str) -> dict[str, str]:
    """Return the {dotted_module_name: file_path} map for the project."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "RETURN f.path AS path",
        project=project,
    )
    paths = [row["path"] for row in rows]
    return {module_name(p): p for p in paths}


def _read_old_qns_for_file(backend: GraphBackend, project: str,
                           file: str) -> set[str]:
    """Return every Function/Method/Class qn currently attributed to ``file``."""
    rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File {path: $file}) "
        "MATCH (f)-[:DEFINES|HAS_METHOD*1..2]->(s) "
        "WHERE s:Function OR s:Method OR s:Class "
        "RETURN DISTINCT s.qualified_name AS qn",
        project=project, file=file,
    )
    return {row["qn"] for row in rows}


def reingest_files(
    root: str, backend: GraphBackend, project: str,
    changeset: ChangeSet, batch_size: int = 1000,
) -> UpdateSummary:
    """Two-phase incremental re-ingest of changed/added/deleted files."""
    if not (changeset.added or changeset.changed or changeset.deleted):
        return UpdateSummary(added=0, changed=0, deleted=0,
                             unchanged=len(changeset.unchanged),
                             parse_errors=0)

    writer = GraphWriter(backend, batch_size=batch_size)

    # ---- Phase A: structural reconcile ---------------------------------

    for rel in changeset.deleted:
        writer.delete_file(project=project, file=rel)

    pending_files: list[tuple[str, list, list]] = []
    parse_errors = 0
    for rel in changeset.added + changeset.changed:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        new_hash = changeset.hashes.get(rel)

        broken = has_errors(parse_source(source))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            writer.write_files(
                project,
                [FileRecord(path=rel, name=os.path.basename(rel),
                            parse_error=True, content_hash=new_hash)],
            )
            continue

        defs, imports, raw_calls = extract(rel, source)
        new_qns = {d.qualified_name for d in defs}

        if rel in changeset.changed:
            old_qns = _read_old_qns_for_file(backend, project, rel)
            removed = old_qns - new_qns
            if removed:
                writer.delete_symbols(sorted(removed))

        writer.write_files(
            project,
            [FileRecord(path=rel, name=os.path.basename(rel),
                        parse_error=False, content_hash=new_hash)],
        )
        writer.write_definitions(defs)
        writer.delete_outgoing_calls_for_file(rel)
        writer.delete_imports_from_file(rel)
        writer.flag_runtime_stale_for_file(project=project, file=rel)
        pending_files.append((rel, imports, raw_calls))

    # ---- Phase B: resolve calls and imports against the final state ----

    if pending_files:
        project_defined = _read_project_defined(backend, project)
        project_modules = _read_project_modules(backend, project)

        for _rel, imports, raw_calls in pending_files:
            edges = resolve_calls(raw_calls, project_defined)
            writer.write_calls(edges)

            resolved_imports = resolve_imports(imports, project_modules)
            _write_imports_for_file(backend, resolved_imports, batch_size)

    return UpdateSummary(
        added=len(changeset.added),
        changed=len(changeset.changed),
        deleted=len(changeset.deleted),
        unchanged=len(changeset.unchanged),
        parse_errors=parse_errors,
    )


def update_files(
    root: str,
    backend: GraphBackend,
    project: str,
    paths: Iterable[str],
    batch_size: int = 1000,
) -> UpdateSummary:
    """Re-ingest exactly the files in ``paths`` (absolute or relative).

    Unlike :func:`reingest_files` (driven by a whole-tree
    :func:`detect_changes` scan), this entry point only hashes the paths
    the caller passes in. Used by the watch loop.
    """
    rels: list[str] = []
    for p in paths:
        ap = os.path.abspath(os.path.join(root, p)) \
            if not os.path.isabs(p) else os.path.abspath(p)
        try:
            rel = os.path.relpath(ap, root)
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        rels.append(rel)

    if not rels:
        return UpdateSummary(0, 0, 0, 0, 0)

    stored_rows = backend.execute(
        "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
        "WHERE f.path IN $paths "
        "RETURN f.path AS path, f.content_hash AS hash",
        project=project, paths=rels,
    )
    stored = {row["path"]: row.get("hash") for row in stored_rows}

    hashes: dict[str, str] = {}
    added: list[str] = []
    changed: list[str] = []
    deleted: list[str] = []
    for rel in rels:
        ap = os.path.join(root, rel)
        if not os.path.exists(ap):
            if rel in stored:
                deleted.append(rel)
            continue
        with open(ap, "rb") as handle:
            h = hashlib.sha256(handle.read()).hexdigest()
        hashes[rel] = h
        if rel not in stored:
            added.append(rel)
        elif stored[rel] != h:
            changed.append(rel)

    if not (added or changed or deleted):
        return UpdateSummary(0, 0, 0, 0, 0)

    cs = ChangeSet(
        added=sorted(added),
        changed=sorted(changed),
        deleted=sorted(deleted),
        unchanged=[],
        hashes=hashes,
    )
    return reingest_files(root, backend, project, cs, batch_size=batch_size)


def _write_imports_for_file(backend: GraphBackend, imports: list,
                            batch_size: int) -> None:
    """Write IMPORTS edges for a single file's resolved imports, batched."""
    files = [i for i in imports if i.target_kind == "file"]
    modules = [i for i in imports if i.target_kind != "file"]
    for start in range(0, len(files), batch_size):
        rows = [{"file": i.file, "target": i.target, "raw": i.raw,
                 "line": i.line}
                for i in files[start:start + batch_size]]
        backend.execute(
            "UNWIND $rows AS row "
            "MATCH (src:File {path: row.file}) "
            "MATCH (dst:File {path: row.target}) "
            "MERGE (src)-[r:IMPORTS]->(dst) "
            "SET r.raw = row.raw, r.line = row.line",
            rows=rows,
        )
    for start in range(0, len(modules), batch_size):
        rows = [{"file": i.file, "target": i.target, "kind": i.target_kind,
                 "raw": i.raw, "line": i.line}
                for i in modules[start:start + batch_size]]
        backend.execute(
            "UNWIND $rows AS row "
            "MATCH (src:File {path: row.file}) "
            "MERGE (m:Module {name: row.target}) SET m.kind = row.kind "
            "MERGE (src)-[r:IMPORTS]->(m) "
            "SET r.raw = row.raw, r.line = row.line",
            rows=rows,
        )
