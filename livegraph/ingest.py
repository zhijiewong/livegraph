"""Phase 1: build the static graph from a Python codebase."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from livegraph.discovery import discover_python_files, discover_typescript_files, module_name
from livegraph.graph.backend import GraphBackend
from livegraph.graph.schema import create_schema
from livegraph.graph.writer import GraphWriter
from livegraph.models import FileRecord
from livegraph.static.extractor import extract
from livegraph.static.parser import has_errors, parse_source
from livegraph.static.resolver import (
    ResolvedImport, resolve_calls, resolve_imports,
)
from livegraph.static_ts.extractor import extract as ts_extract
from livegraph.static_ts.parser import (
    has_errors as ts_has_errors, is_jsx_file, parse_source as ts_parse_source,
)
from livegraph.static_ts.resolver import (
    resolve_calls as ts_resolve_calls,
    resolve_imports as ts_resolve_imports,
)
from livegraph.static_ts.tsconfig import load_tsconfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestSummary:
    """Counts produced by a Phase 1 run."""

    files: int
    definitions: int
    call_edges: int
    parse_errors: int



def ingest_project(
    root: str, backend: GraphBackend, project_name: str | None = None,
    batch_size: int = 1000, lang: str = "auto",
) -> IngestSummary:
    """Run Phase 1: discover, parse, resolve, and write the static graph.

    ``lang``: ``"auto"`` (default, runs both Python and TS pipelines on
    whichever extensions are present), ``"python"``, or ``"typescript"``.
    """
    if lang not in ("auto", "python", "typescript"):
        raise ValueError(
            f"invalid lang {lang!r}; must be 'auto', 'python', or 'typescript'"
        )
    project_name = project_name or os.path.basename(os.path.abspath(root))
    backend.verify()
    create_schema(backend)
    writer = GraphWriter(backend, batch_size=batch_size)

    do_py = lang in ("auto", "python")
    do_ts = lang in ("auto", "typescript")

    py_rels = sorted(discover_python_files(root)) if do_py else []
    ts_rels = sorted(discover_typescript_files(root)) if do_ts else []

    file_records: list[FileRecord] = []
    py_defs: list = []
    ts_defs: list = []
    py_imports: list = []
    ts_imports: list = []
    py_raw_calls: list = []
    ts_raw_calls: list = []
    parse_errors = 0

    # Python files.
    project_modules = {module_name(p): p for p in py_rels}
    for rel in py_rels:
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
        defs, imps, raw_calls = extract(rel, source)
        py_defs.extend(defs)
        py_imports.extend(imps)
        py_raw_calls.extend(raw_calls)

    # TypeScript files.
    ts_files_set = set(ts_rels)
    tsconfig = load_tsconfig(root)
    for rel in ts_rels:
        with open(os.path.join(root, rel), "rb") as handle:
            source = handle.read()
        content_hash = hashlib.sha256(source).hexdigest()
        broken = ts_has_errors(ts_parse_source(source, jsx=is_jsx_file(rel)))
        file_records.append(FileRecord(
            path=rel, name=os.path.basename(rel), parse_error=broken,
            content_hash=content_hash))
        if broken:
            parse_errors += 1
            logger.warning("skipping unparseable file: %s", rel)
            continue
        defs, imps, raw_calls = ts_extract(rel, source)
        ts_defs.extend(defs)
        ts_imports.extend(imps)
        ts_raw_calls.extend(raw_calls)

    # Per-language resolution to avoid cross-language false-positive
    # name matches.
    py_defined = {d.qualified_name for d in py_defs}
    ts_defined = {d.qualified_name for d in ts_defs}
    call_edges = (
        resolve_calls(py_raw_calls, py_defined)
        + ts_resolve_calls(ts_raw_calls, ts_defined)
    )

    resolved_imports = (
        resolve_imports(py_imports, project_modules)
        + ts_resolve_imports(
            ts_imports, project_files=ts_files_set, tsconfig=tsconfig,
        )
    )
    all_defs = py_defs + ts_defs

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
