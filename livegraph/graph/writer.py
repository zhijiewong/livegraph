"""Batched, idempotent Cypher writes for livegraph records."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, TypeVar

from livegraph.graph.backend import GraphBackend
from livegraph.models import CallEdge, Definition, FileRecord

_T = TypeVar("_T")

# Label per Definition.kind.
_DEF_LABEL = {"class": "Class", "function": "Function", "method": "Method"}


def _batched(items: Iterable[_T], size: int) -> Iterator[list[_T]]:
    """Yield ``items`` in chunks of at most ``size``."""
    batch: list[_T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class GraphWriter:
    """Writes model records to a ``GraphBackend`` in idempotent batches."""

    def __init__(self, backend: GraphBackend, batch_size: int = 1000) -> None:
        self._backend = backend
        self._batch_size = batch_size

    def write_files(self, project: str, files: Iterable[FileRecord]) -> None:
        """MERGE File nodes and CONTAINS edges from the Project node."""
        for batch in _batched(files, self._batch_size):
            rows = [
                {"path": f.path, "name": f.name,
                 "language": f.language, "parse_error": f.parse_error}
                for f in batch
            ]
            self._backend.execute(
                "MERGE (p:Project {name: $project}) "
                "WITH p UNWIND $rows AS row "
                "MERGE (f:File {path: row.path}) "
                "SET f.name = row.name, f.language = row.language, "
                "    f.parse_error = row.parse_error "
                "MERGE (p)-[:CONTAINS]->(f)",
                project=project, rows=rows,
            )

    def write_definitions(self, definitions: Iterable[Definition]) -> None:
        """MERGE Class/Function/Method nodes with their structural edges."""
        for batch in _batched(definitions, self._batch_size):
            for kind, label in _DEF_LABEL.items():
                rows = [self._def_row(d) for d in batch if d.kind == kind]
                if not rows:
                    continue
                if kind == "method":
                    self._write_methods(rows)
                else:
                    self._write_file_definitions(label, rows)

    def write_calls(self, edges: Iterable[CallEdge]) -> None:
        """MERGE CALLS edges, setting provenance properties."""
        for batch in _batched(edges, self._batch_size):
            rows = [
                {"caller": e.caller_qn, "callee": e.callee_qn,
                 "static": e.static, "runtime": e.runtime,
                 "observed_count": e.observed_count,
                 "call_site_lines": list(e.call_site_lines)}
                for e in batch
            ]
            self._backend.execute(
                "UNWIND $rows AS row "
                "MATCH (caller {qualified_name: row.caller}) "
                "MATCH (callee {qualified_name: row.callee}) "
                "MERGE (caller)-[c:CALLS]->(callee) "
                "SET c.static = row.static, c.runtime = row.runtime, "
                "    c.observed_count = row.observed_count, "
                "    c.call_site_lines = row.call_site_lines",
                rows=rows,
            )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _def_row(d: Definition) -> dict[str, Any]:
        return {
            "qualified_name": d.qualified_name, "name": d.name, "file": d.file,
            "start_line": d.start_line, "end_line": d.end_line,
            "decorators": list(d.decorators), "source": d.source,
            "parent_class": d.parent_class,
        }

    def _write_file_definitions(self, label: str, rows: list[dict[str, Any]]) -> None:
        self._backend.execute(
            f"UNWIND $rows AS row "
            f"MATCH (file:File {{path: row.file}}) "
            f"MERGE (d:{label} {{qualified_name: row.qualified_name}}) "
            f"SET d.name = row.name, d.file = row.file, "
            f"    d.start_line = row.start_line, d.end_line = row.end_line, "
            f"    d.decorators = row.decorators, d.source = row.source "
            f"MERGE (file)-[:DEFINES]->(d)",
            rows=rows,
        )

    def _write_methods(self, rows: list[dict[str, Any]]) -> None:
        self._backend.execute(
            "UNWIND $rows AS row "
            "MATCH (cls:Class {qualified_name: row.parent_class}) "
            "MERGE (m:Method {qualified_name: row.qualified_name}) "
            "SET m.name = row.name, m.file = row.file, "
            "    m.start_line = row.start_line, m.end_line = row.end_line, "
            "    m.decorators = row.decorators, m.source = row.source, "
            "    m.class = row.parent_class "
            "MERGE (cls)-[:HAS_METHOD]->(m)",
            rows=rows,
        )
