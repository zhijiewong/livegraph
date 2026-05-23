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


# -- runtime_only_calls / dead_static_calls ---------------------------

_CALL_PAIR_RETURN = (
    "RETURN caller.qualified_name AS caller_qualified_name, "
    "       caller.name AS caller_name, "
    "       head([l IN labels(caller) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS caller_kind, "
    "       caller.file AS caller_file, "
    "       caller.start_line AS caller_start_line, "
    "       caller.end_line AS caller_end_line, "
    "       callee.qualified_name AS callee_qualified_name, "
    "       callee.name AS callee_name, "
    "       head([l IN labels(callee) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS callee_kind, "
    "       callee.file AS callee_file, "
    "       callee.start_line AS callee_start_line, "
    "       callee.end_line AS callee_end_line, "
    "       coalesce(c.observed_count, 0) AS observed_count "
)

_RUNTIME_ONLY_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(caller)-[c:CALLS]->(callee) "
    "WHERE c.runtime = true AND coalesce(c.static, false) = false "
    + _CALL_PAIR_RETURN +
    "LIMIT $limit"
)

_DEAD_STATIC_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(caller)-[c:CALLS]->(callee) "
    "WHERE c.static = true AND coalesce(c.runtime, false) = false "
    + _CALL_PAIR_RETURN +
    "LIMIT $limit"
)


def _pair_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "caller": _symbol_from_row({
            "qualified_name": row["caller_qualified_name"],
            "name": row["caller_name"], "kind": row["caller_kind"],
            "file": row["caller_file"],
            "start_line": row["caller_start_line"],
            "end_line": row["caller_end_line"],
        }),
        "callee": _symbol_from_row({
            "qualified_name": row["callee_qualified_name"],
            "name": row["callee_name"], "kind": row["callee_kind"],
            "file": row["callee_file"],
            "start_line": row["callee_start_line"],
            "end_line": row["callee_end_line"],
        }),
    }


def runtime_only_calls(
    backend: GraphBackend, project: str, file: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Calls observed at runtime that static analysis did NOT predict.

    This is the headline livegraph query — the dynamic-dispatch edges
    no purely static code-graph tool can produce.
    """
    rows = backend.execute(
        _RUNTIME_ONLY_CYPHER, project=project, file=file, limit=limit,
    )
    return [
        {**_pair_from_row(r),
         "observed_count": int(r.get("observed_count") or 0)}
        for r in rows
    ]


def dead_static_calls(
    backend: GraphBackend, project: str, file: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Calls predicted by static analysis but never observed at runtime."""
    rows = backend.execute(
        _DEAD_STATIC_CYPHER, project=project, file=file, limit=limit,
    )
    return [_pair_from_row(r) for r in rows]


# -- tests_for / untested_symbols -------------------------------------

_TESTS_FOR_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(symbol) "
    "WHERE symbol.qualified_name = $qualified_name "
    "MATCH (t:Test)-[c:COVERS]->(symbol) "
    "RETURN t.qualified_name AS qualified_name, t.name AS name, "
    "       head([l IN labels(t) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       t.file AS file, t.start_line AS start_line, "
    "       t.end_line AS end_line, "
    "       coalesce(t.test_outcome, '') AS test_outcome, "
    "       coalesce(t.test_duration, 0.0) AS test_duration, "
    "       coalesce(c.lines_covered, 0) AS lines_covered, "
    "       coalesce(c.lines_total, 0) AS lines_total, "
    "       coalesce(c.coverage_pct, 0.0) AS coverage_pct "
    "ORDER BY t.qualified_name"
)

_UNTESTED_SYMBOLS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE coalesce(s.runtime_observed, false) = false "
    "  AND ("
    "    ($kind = 'any' AND (s:Function OR s:Method)) "
    "    OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "    OR ($kind = 'method' AND s:Method) "
    "  ) "
    "RETURN s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line "
    "ORDER BY s.qualified_name "
    "LIMIT $limit"
)


def tests_for(backend: GraphBackend, project: str,
              qualified_name: str) -> list[dict[str, Any]]:
    """Return tests that cover ``qualified_name``, with coverage data."""
    rows = backend.execute(
        _TESTS_FOR_CYPHER, project=project, qualified_name=qualified_name,
    )
    return [
        {
            "test": {
                **_symbol_from_row(r),
                "test_outcome": r.get("test_outcome") or "",
                "test_duration": float(r.get("test_duration") or 0.0),
            },
            "lines_covered": int(r.get("lines_covered") or 0),
            "lines_total": int(r.get("lines_total") or 0),
            "coverage_pct": float(r.get("coverage_pct") or 0.0),
        }
        for r in rows
    ]


def untested_symbols(
    backend: GraphBackend, project: str, file: str | None = None,
    kind: str = "any", limit: int = 100,
) -> list[dict[str, Any]]:
    """Functions/methods that the test suite never exercised."""
    rows = backend.execute(
        _UNTESTED_SYMBOLS_CYPHER, project=project, file=file,
        kind=kind, limit=limit,
    )
    return [_symbol_from_row(r) for r in rows]


# -- imports / graph_status -------------------------------------------

_IMPORTS_OUT_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File "
    "    {path: $file})-[r:IMPORTS]->(t) "
    "RETURN coalesce(t.path, t.name) AS target, "
    "       CASE WHEN t:File THEN 'file' ELSE t.kind END AS kind, "
    "       r.raw AS raw, r.line AS line "
    "ORDER BY r.line"
)

_IMPORTS_IN_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[r:IMPORTS]->(dst:File {path: $file}) "
    "RETURN src.path AS source_file, r.raw AS raw, r.line AS line "
    "ORDER BY src.path, r.line"
)

_GRAPH_STATUS_CYPHER = (
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
    "WITH count(DISTINCT f) AS files "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(c:Class) "
    "WITH files, count(DISTINCT c) AS classes "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(fn:Function) WHERE NOT fn:Test "
    "WITH files, classes, count(DISTINCT fn) AS functions "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(:Class)-[:HAS_METHOD]->(m:Method) "
    "WITH files, classes, functions, count(DISTINCT m) AS methods "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES]->(t:Test) "
    "WITH files, classes, functions, methods, "
    "     count(DISTINCT t) AS tests "
    "OPTIONAL MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(:Function|:Method)"
    "-[ec:CALLS]->() "
    "WITH files, classes, functions, methods, tests, "
    "     count(DISTINCT ec) AS calls_total, "
    "     sum(CASE WHEN coalesce(ec.runtime,false) AND NOT coalesce(ec.static,false) "
    "              THEN 1 ELSE 0 END) AS calls_runtime_only, "
    "     sum(CASE WHEN coalesce(ec.static,false) AND NOT coalesce(ec.runtime,false) "
    "              THEN 1 ELSE 0 END) AS calls_static_only, "
    "     sum(CASE WHEN coalesce(ec.static,false) AND coalesce(ec.runtime,false) "
    "              THEN 1 ELSE 0 END) AS calls_both "
    "RETURN $project AS project, files, classes, functions, methods, "
    "       tests, calls_total, calls_runtime_only, "
    "       calls_static_only, calls_both"
)


def imports(backend: GraphBackend, project: str, file: str,
            direction: str = "out") -> list[dict[str, Any]]:
    """Imports out of (or into) ``file`` within the project."""
    if direction == "out":
        rows = backend.execute(
            _IMPORTS_OUT_CYPHER, project=project, file=file,
        )
        return [
            {"target": r["target"], "kind": r.get("kind") or "thirdparty",
             "raw": r.get("raw") or "", "line": int(r.get("line") or 0)}
            for r in rows
        ]
    if direction == "in":
        rows = backend.execute(
            _IMPORTS_IN_CYPHER, project=project, file=file,
        )
        return [
            {"source_file": r["source_file"],
             "raw": r.get("raw") or "", "line": int(r.get("line") or 0)}
            for r in rows
        ]
    raise ValueError(
        f"imports direction must be 'out' or 'in', got {direction!r}"
    )


_GRAPH_STATUS_KEYS = (
    "project", "files", "classes", "functions", "methods", "tests",
    "calls_total", "calls_runtime_only", "calls_static_only", "calls_both",
)


def graph_status(backend: GraphBackend,
                 project: str) -> dict[str, Any]:
    """Aggregate counts for the configured project."""
    rows = backend.execute(_GRAPH_STATUS_CYPHER, project=project)
    if not rows:
        return {
            "project": project,
            **{k: 0 for k in _GRAPH_STATUS_KEYS if k != "project"},
        }
    row = rows[0]
    return {
        "project": row.get("project") or project,
        **{k: int(row.get(k) or 0) for k in _GRAPH_STATUS_KEYS
           if k != "project"},
    }
