"""Typed records exchanged between livegraph's modules.

Phase-1 (static) records only. Runtime records are added in a later task.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FileRecord:
    """A source file discovered during ingestion."""

    path: str            # project-relative, forward-slash separated
    name: str            # basename
    language: str = "python"
    parse_error: bool = False
    content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class Definition:
    """A class, function, or method definition extracted from an AST."""

    qualified_name: str
    name: str
    kind: str            # "function" | "class" | "method"
    file: str            # project-relative path
    start_line: int
    end_line: int
    decorators: tuple[str, ...]
    source: str
    parent_class: str | None = None   # qualified_name of the owning class


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """A single import statement, before resolution."""

    file: str            # importing file, project-relative
    raw: str             # raw statement text
    line: int
    module: str          # dotted module name being imported


@dataclass(frozen=True, slots=True)
class CallEdge:
    """A call relationship between two definitions, with provenance."""

    caller_qn: str
    callee_qn: str
    static: bool = False
    runtime: bool = False
    observed_count: int = 0
    call_site_lines: tuple[int, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class RuntimeCall:
    """A caller->callee call observed during a traced test run."""

    caller_qn: str
    callee_qn: str
    test_qn: str
    call_site_line: int


@dataclass(frozen=True, slots=True)
class TestResult:
    """The outcome of a single executed test."""

    qualified_name: str
    outcome: str         # "passed" | "failed" | "skipped"
    duration: float


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    """Per-test coverage of one definition."""

    test_qn: str
    symbol_qn: str
    lines_covered: int
    lines_total: int

    @property
    def coverage_pct(self) -> float:
        if self.lines_total == 0:
            return 0.0
        return round(100.0 * self.lines_covered / self.lines_total, 2)
