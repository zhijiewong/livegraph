"""Attribute per-test coverage lines to definitions."""
from __future__ import annotations

from collections.abc import Iterable

from livegraph.models import CoverageRecord, Definition


def map_coverage_to_symbols(
    per_test_lines: dict[str, set[tuple[str, int]]],
    definitions: Iterable[Definition],
) -> list[CoverageRecord]:
    """Build CoverageRecords from per-test covered (file, line) pairs.

    ``per_test_lines`` maps a test qualified_name to the set of
    ``(rel_path, line_number)`` pairs executed during that test.
    A line is attributed to the definition whose line span contains it.
    Functions and methods are attributed; classes are skipped (their
    coverage is the union of their methods).
    """
    defs = [d for d in definitions if d.kind in ("function", "method")]
    records: list[CoverageRecord] = []

    for test_qn, lines in per_test_lines.items():
        for definition in defs:
            span = range(definition.start_line, definition.end_line + 1)
            total = len(span)
            covered = sum(
                1 for line in span
                if (definition.file, line) in lines
            )
            if covered > 0:
                records.append(CoverageRecord(
                    test_qn=test_qn, symbol_qn=definition.qualified_name,
                    lines_covered=covered, lines_total=total,
                ))
    return records
