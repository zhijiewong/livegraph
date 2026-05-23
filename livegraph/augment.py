"""Phase 2: merge runtime observations into the existing graph."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.graph.writer import GraphWriter
from livegraph.models import CoverageRecord, Definition, RuntimeCall, TestResult
from livegraph.runtime.coverage_adapter import map_coverage_to_symbols

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AugmentSummary:
    """Counts produced by a Phase 2 run."""

    runtime_call_edges: int
    tests: int
    coverage_edges: int


def _load_definitions(backend: GraphBackend) -> list[Definition]:
    """Read function/method definitions back from the graph.

    Phase 2 needs line spans to attribute coverage; they were written
    by Phase 1, so they are read rather than re-parsed.
    """
    rows = backend.execute(
        "MATCH (d) WHERE d:Function OR d:Method "
        "RETURN d.qualified_name AS qualified_name, d.file AS file, "
        "       d.start_line AS start_line, d.end_line AS end_line, "
        "       labels(d) AS labels"
    )
    definitions: list[Definition] = []
    for row in rows:
        kind = "method" if "Method" in (row.get("labels") or []) else "function"
        definitions.append(Definition(
            qualified_name=row["qualified_name"], name="", kind=kind,
            file=row["file"], start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0, decorators=(), source="",
        ))
    return definitions


def augment_from_observations(
    observations: dict[str, Any], backend: GraphBackend,
    batch_size: int = 1000,
) -> AugmentSummary:
    """Merge a runtime observations dict into the graph."""
    backend.verify()
    writer = GraphWriter(backend, batch_size=batch_size)

    runtime_calls = [
        RuntimeCall(caller_qn=rc["caller_qn"], callee_qn=rc["callee_qn"],
                    test_qn=rc["test_qn"], call_site_line=0)
        for rc in observations.get("runtime_calls", [])
    ]
    counts: dict[tuple[str, str], int] = {}
    for rc in observations.get("runtime_calls", []):
        key = (rc["caller_qn"], rc["callee_qn"])
        counts[key] = counts.get(key, 0) + int(rc["observed_count"])

    tests = [
        TestResult(qualified_name=t["qualified_name"], outcome=t["outcome"],
                   duration=float(t["duration"]))
        for t in observations.get("tests", [])
    ]

    per_test_lines: dict[str, set[tuple[str, int]]] = {}
    for entry in observations.get("coverage", []):
        bucket = per_test_lines.setdefault(entry["test_context"], set())
        for line in entry["lines"]:
            bucket.add((entry["file"], int(line)))
    coverage_records: list[CoverageRecord] = map_coverage_to_symbols(
        per_test_lines, _load_definitions(backend))

    writer.write_test_results(tests)
    writer.write_runtime_calls(runtime_calls, counts)
    writer.write_coverage(coverage_records)

    # Phase 5: set runtime_stale=false on every symbol observed in this
    # trace run. Symbols not observed keep whatever flag they had.
    observed_qns: set[str] = set()
    for rc in observations.get("runtime_calls", []):
        observed_qns.add(rc["caller_qn"])
        observed_qns.add(rc["callee_qn"])
    for t in observations.get("tests", []):
        observed_qns.add(t["qualified_name"])
    for record in coverage_records:
        observed_qns.add(record.symbol_qn)
    writer.clear_runtime_stale_for_symbols(sorted(observed_qns))

    logger.info("Phase 2: %d runtime calls, %d tests, %d coverage edges",
                len(runtime_calls), len(tests), len(coverage_records))
    return AugmentSummary(
        runtime_call_edges=len(runtime_calls), tests=len(tests),
        coverage_edges=len(coverage_records),
    )
