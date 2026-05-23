"""Safety pipeline for agent-submitted Cypher queries.

Pure functions: ``forbidden_keyword``, ``auto_limit``, ``inject_project``.
Typed exception classes for each error class that ``run_cypher`` can raise.
The actual read-transaction execution lives on the GraphBackend so this
module stays dependency-free and trivially unit-testable.
"""
from __future__ import annotations

import re
from typing import Any

# Rejects write clauses, schema management, bulk-load, and any procedure call.
# Multi-word forms listed first so the alternation matches them whole.
_FORBIDDEN = re.compile(
    r"\b(DETACH\s+DELETE|LOAD\s+CSV|USING\s+PERIODIC\s+COMMIT|"
    r"CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL)\b",
    re.IGNORECASE,
)

_TRAILING_LIMIT = re.compile(r"\bLIMIT\b\s+\d+\s*;?\s*$", re.IGNORECASE)


def forbidden_keyword(query: str) -> str | None:
    """Return the first forbidden keyword found, uppercased, or None."""
    match = _FORBIDDEN.search(query)
    if match is None:
        return None
    return " ".join(match.group(1).upper().split())


def auto_limit(query: str, row_limit: int) -> str:
    """Append ``LIMIT row_limit`` if the query has no trailing LIMIT clause."""
    if _TRAILING_LIMIT.search(query) is not None:
        return query
    stripped = query.rstrip().rstrip(";").rstrip()
    return f"{stripped} LIMIT {row_limit}"


def inject_project(params: dict[str, Any] | None,
                   project: str) -> dict[str, Any]:
    """Return a copy of ``params`` with ``$project`` defaulted (not overridden)."""
    out = dict(params or {})
    out.setdefault("project", project)
    return out


class CypherError(Exception):
    """Base class for errors run_cypher can return to the caller."""

    code: str = "cypher_error"


class ForbiddenKeywordError(CypherError):
    code = "forbidden_keyword"

    def __init__(self, keyword: str, query: str) -> None:
        super().__init__(f"forbidden_keyword: {keyword}")
        self.keyword = keyword
        self.query = query


class CypherSyntaxError(CypherError):
    code = "cypher_syntax"

    def __init__(self, message: str, query: str) -> None:
        super().__init__(f"cypher_syntax: {message}")
        self.message = message
        self.query = query


class CypherTimeoutError(CypherError):
    code = "timeout"

    def __init__(self, seconds: int, query: str) -> None:
        super().__init__(f"timeout: query exceeded {seconds}s")
        self.seconds = seconds
        self.query = query


class EngineWriteAttemptedError(CypherError):
    code = "engine_write_attempted"

    def __init__(self, query: str) -> None:
        super().__init__("engine_write_attempted")
        self.query = query
