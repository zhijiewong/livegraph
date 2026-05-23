"""Phase 1: build the static graph from a Python codebase."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from livegraph.discovery import discover_python_files
from livegraph.graph.backend import GraphBackend
from livegraph.graph.schema import create_schema
from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord
from livegraph.static.extractor import extract
from livegraph.static.parser import has_errors, parse_source
from livegraph.static.resolver import (
    ResolvedImport, resolve_calls, resolve_imports,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestSummary:
    """Counts produced by a Phase 1 run."""

    files: int
    definitions: int
    call_edges: int
    parse_errors: int


def _module_name(rel_path: str) -> str:
    """Dotted module name for a project-relative file path."""
    no_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = no_ext.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def ingest_project(
    root: str, backend: GraphBackend, project_name: str | None = None,
    batch_size: int = 1000,
) -> IngestSummary:
    """Run Phase 1: discover, parse, resolve, and write the static graph."""
    project_name = project_name or os.path.basename(os.path.abspath(root))
    backend.verify()
    create_schema(backend)
    writer = GraphWriter(backend, batch_size=batch_size)

    rel_paths = sorted(discover_python_files(root))
    project_modules = {_module_name(p): p for p in rel_paths}

    file_records: list[FileRecord] = []
    all_defs = []
    all_imports = []
    all_raw_calls = []
    parse_errors = 0

    for rel in rel_paths:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        content_hash = hashlib.sha256(source).hexdigest()
        broken = has_errors(parse_source(source))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken,
            content_hash=content_hash))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            continue
        defs, imports, raw_calls = extract(rel, source)
        all_defs.extend(defs)
        all_imports.extend(imports)
        all_raw_calls.extend(raw_calls)

    defined = {d.qualified_name for d in all_defs}
    call_edges = resolve_calls(all_raw_calls, defined)
    resolved_imports = resolve_imports(all_imports, project_modules)

    writer.write_files(project_name, file_records,
                       root_path=os.path.abspath(root))
    writer.write_definitions(all_defs)
    _write_imports(backend, resolved_imports, batch_size)
    writer.write_calls(call_edges)

    return IngestSummary(
        files=len(file_records), definitions=len(all_defs),
        call_edges=len(call_edges), parse_errors=parse_errors,
    )


def _write_imports(
    backend: GraphBackend, imports: list[ResolvedImport], batch_size: int,
) -> None:
    """MERGE IMPORTS edges to File or Module targets, batched."""
    files = [i for i in imports if i.target_kind == "file"]
    modules = [i for i in imports if i.target_kind != "file"]
    for start in range(0, len(files), batch_size):
        rows = [{"file": i.file, "target": i.target, "raw": i.raw,
                 "line": i.line} for i in files[start:start + batch_size]]
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
