"""Pure-function MCP tool implementations.

Each tool function takes the GraphBackend and project name explicitly so
it is trivially unit-testable with FakeBackend. The MCP server in
``server.py`` is the only place that holds backend/project state and
wraps these functions for FastMCP registration.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend

# Labels we treat as a primary "kind" for SymbolRef.
_KIND_LABELS = ("Function", "Method", "Class")


def _kind_from_labels(labels: list[str] | None) -> str | None:
    """Return the first known kind label found in ``labels``, lowercased."""
    if not labels:
        return None
    for label in labels:
        if label in _KIND_LABELS:
            return label.lower()
    return None


def _symbol_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a Cypher row into the canonical SymbolRef shape.

    The Cypher query is responsible for returning these exact keys.
    """
    return {
        "qualified_name": row["qualified_name"],
        "name": row["name"],
        "kind": row["kind"],
        "file": row["file"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
    }


# -- find_symbol -------------------------------------------------------

_FIND_SYMBOL_CYPHER = (
    "MATCH (p:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE (s:Function OR s:Method OR s:Class) AND ("
    "  ($exact AND s.name = $query) "
    "  OR (NOT $exact AND toLower(s.name) CONTAINS toLower($query)) "
    ") "
    "RETURN s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line "
    "ORDER BY s.qualified_name "
    "LIMIT $limit"
)


def find_symbol(backend: GraphBackend, project: str, query: str,
                exact: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """Find symbols by name. Substring (case-insensitive) unless ``exact``."""
    rows = backend.execute(
        _FIND_SYMBOL_CYPHER,
        project=project, query=query, exact=exact, limit=limit,
    )
    return [_symbol_from_row(r) for r in rows]


# -- get_source --------------------------------------------------------

_GET_SOURCE_CYPHER = (
    "MATCH (p:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE s.qualified_name = $qualified_name "
    "RETURN s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line, "
    "       coalesce(s.decorators, []) AS decorators, "
    "       coalesce(s.source, '') AS source, "
    "       coalesce(s.runtime_observed, false) AS runtime_observed, "
    "       coalesce(s.coverage_pct, 0.0) AS coverage_pct "
    "LIMIT 1"
)


def get_source(backend: GraphBackend, project: str,
               qualified_name: str) -> dict[str, Any] | None:
    """Return the full source + metadata for a symbol, or None."""
    rows = backend.execute(
        _GET_SOURCE_CYPHER, project=project, qualified_name=qualified_name,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        **_symbol_from_row(row),
        "decorators": list(row.get("decorators") or []),
        "source": row.get("source") or "",
        "runtime_observed": bool(row.get("runtime_observed")),
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
    }


# -- find_callers / find_callees --------------------------------------

_PROVENANCE_PREDICATE = (
    "($provenance = 'any' "
    " OR ($provenance = 'static' AND c.static = true) "
    " OR ($provenance = 'runtime' AND c.runtime = true))"
)

_FIND_CALLERS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(callee) "
    "WHERE callee.qualified_name = $qualified_name "
    "MATCH (caller)-[c:CALLS]->(callee) "
    f"WHERE {_PROVENANCE_PREDICATE} "
    "RETURN caller.qualified_name AS qualified_name, "
    "       caller.name AS name, "
    "       head([l IN labels(caller) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       caller.file AS file, caller.start_line AS start_line, "
    "       caller.end_line AS end_line, "
    "       c.static AS static, coalesce(c.runtime, false) AS runtime, "
    "       coalesce(c.observed_count, 0) AS observed_count, "
    "       coalesce(c.call_site_lines, []) AS call_site_lines "
    "LIMIT $limit"
)

_FIND_CALLEES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(caller) "
    "WHERE caller.qualified_name = $qualified_name "
    "MATCH (caller)-[c:CALLS]->(callee) "
    f"WHERE {_PROVENANCE_PREDICATE} "
    "RETURN callee.qualified_name AS qualified_name, "
    "       callee.name AS name, "
    "       head([l IN labels(callee) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       callee.file AS file, callee.start_line AS start_line, "
    "       callee.end_line AS end_line, "
    "       c.static AS static, coalesce(c.runtime, false) AS runtime, "
    "       coalesce(c.observed_count, 0) AS observed_count, "
    "       coalesce(c.call_site_lines, []) AS call_site_lines "
    "LIMIT $limit"
)


def _edge_provenance(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "static": bool(row.get("static")),
        "runtime": bool(row.get("runtime")),
        "observed_count": int(row.get("observed_count") or 0),
        "call_site_lines": list(row.get("call_site_lines") or []),
    }


def find_callers(backend: GraphBackend, project: str, qualified_name: str,
                 provenance: str = "any",
                 limit: int = 50) -> list[dict[str, Any]]:
    """Return who calls ``qualified_name``, filtered by ``provenance``."""
    rows = backend.execute(
        _FIND_CALLERS_CYPHER, project=project,
        qualified_name=qualified_name, provenance=provenance, limit=limit,
    )
    return [
        {"caller": _symbol_from_row(r), "edge": _edge_provenance(r)}
        for r in rows
    ]


def find_callees(backend: GraphBackend, project: str, qualified_name: str,
                 provenance: str = "any",
                 limit: int = 50) -> list[dict[str, Any]]:
    """Return what ``qualified_name`` calls, filtered by ``provenance``."""
    rows = backend.execute(
        _FIND_CALLEES_CYPHER, project=project,
        qualified_name=qualified_name, provenance=provenance, limit=limit,
    )
    return [
        {"callee": _symbol_from_row(r), "edge": _edge_provenance(r)}
        for r in rows
    ]
