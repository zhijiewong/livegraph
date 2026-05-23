"""Pure-function MCP tool implementations.

Each tool function takes the GraphBackend and project name explicitly so
it is trivially unit-testable with FakeBackend. The MCP server in
``server.py`` is the only place that holds backend/project state and
wraps these functions for FastMCP registration.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.mcp.cypher_guard import (
    CypherSyntaxError, CypherTimeoutError,
    EngineWriteAttemptedError, ForbiddenKeywordError,
    auto_limit, forbidden_keyword, inject_project,
)
from livegraph.mcp.diff_parser import parse_diff
from livegraph.semantic.embed import INDEX_NAME
from livegraph.semantic.provider import EmbeddingProvider

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
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(caller) "
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
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(callee) "
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
    "ORDER BY t.qualified_name LIMIT $limit"
)

_UNTESTED_SYMBOLS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File) "
    "WHERE $file IS NULL OR file.path = $file "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE coalesce(s.runtime_observed, false) = false "
    "  AND ("
    "    ($kind = 'any' AND (s:Function OR s:Method) AND NOT s:Test) "
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
              qualified_name: str,
              limit: int = 50) -> list[dict[str, Any]]:
    """Return tests that cover ``qualified_name``, with coverage data."""
    rows = backend.execute(
        _TESTS_FOR_CYPHER, project=project, qualified_name=qualified_name,
        limit=limit,
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
    "ORDER BY r.line LIMIT $limit"
)

_IMPORTS_IN_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[r:IMPORTS]->(dst:File {path: $file}) "
    "RETURN src.path AS source_file, r.raw AS raw, r.line AS line "
    "ORDER BY src.path, r.line LIMIT $limit"
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
    "-[:DEFINES|HAS_METHOD*1..2]->(caller_ec)"
    "-[ec:CALLS]->() "
    "WHERE caller_ec:Function OR caller_ec:Method "
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
            direction: str = "out",
            limit: int = 100) -> list[dict[str, Any]]:
    """Imports out of (or into) ``file`` within the project."""
    if direction == "out":
        rows = backend.execute(
            _IMPORTS_OUT_CYPHER, project=project, file=file, limit=limit,
        )
        return [
            {"target": r["target"], "kind": r.get("kind") or "thirdparty",
             "raw": r.get("raw") or "", "line": int(r.get("line") or 0)}
            for r in rows
        ]
    if direction == "in":
        rows = backend.execute(
            _IMPORTS_IN_CYPHER, project=project, file=file, limit=limit,
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


# -- change_impact ----------------------------------------------------

_MAX_DEPTH_MIN = 1
_MAX_DEPTH_MAX = 20

# Query A — changed symbols
_CHANGE_IMPACT_QUERY_A = (
    "UNWIND $files AS spec "
    "MATCH (:Project {name: $project})-[:CONTAINS]->(file:File "
    "    {path: spec.path}) "
    "MATCH (file)-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE (s:Function OR s:Method) "
    "  AND any(line IN spec.lines WHERE "
    "          line >= s.start_line AND line <= s.end_line) "
    "RETURN DISTINCT s.qualified_name AS qualified_name, s.name AS name, "
    "       head([l IN labels(s) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       s.file AS file, s.start_line AS start_line, "
    "       s.end_line AS end_line, "
    "       coalesce(s.runtime_observed, false) AS runtime_observed, "
    "       coalesce(s.coverage_pct, 0.0) AS coverage_pct"
)

# Query C — tests for any (changed ∪ impacted) symbol
_CHANGE_IMPACT_QUERY_C = (
    "UNWIND $all_affected_qns AS qn "
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE s.qualified_name = qn "
    "MATCH (t:Test)-[c:COVERS]->(s) "
    "RETURN DISTINCT t.qualified_name AS qualified_name, t.name AS name, "
    "       head([l IN labels(t) WHERE l IN ['Function','Method','Class'] "
    "             | toLower(l)]) AS kind, "
    "       t.file AS file, t.start_line AS start_line, "
    "       t.end_line AS end_line, "
    "       coalesce(t.test_outcome, '') AS test_outcome, "
    "       collect(DISTINCT qn) AS covers_symbols, "
    "       avg(coalesce(c.coverage_pct, 0.0)) AS avg_coverage_pct "
    "ORDER BY t.qualified_name"
)


def _query_b_cypher(max_depth: int) -> str:
    """Build the impacted-callers query with a safely-interpolated depth."""
    return (
        "UNWIND $changed_qns AS changed_qn "
        "MATCH (changed {qualified_name: changed_qn}) "
        "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
        "-[:DEFINES|HAS_METHOD*1..2]->(impacted) "
        f"MATCH path = (impacted)-[:CALLS*1..{max_depth}]->(changed) "
        "WHERE all(rel IN relationships(path) WHERE "
        "          ($provenance = 'any') "
        "       OR ($provenance = 'static'  AND rel.static  = true) "
        "       OR ($provenance = 'runtime' AND rel.runtime = true)) "
        "WITH impacted, changed_qn, length(path) AS depth, "
        "     [r IN relationships(path) | "
        "       {static: coalesce(r.static, false), "
        "        runtime: coalesce(r.runtime, false)}] AS edge_provenance "
        "RETURN impacted.qualified_name AS qualified_name, "
        "       impacted.name AS name, "
        "       head([l IN labels(impacted) "
        "             WHERE l IN ['Function','Method','Class'] "
        "             | toLower(l)]) AS kind, "
        "       impacted.file AS file, "
        "       impacted.start_line AS start_line, "
        "       impacted.end_line AS end_line, "
        "       coalesce(impacted.runtime_observed, false) "
        "         AS runtime_observed, "
        "       coalesce(impacted.coverage_pct, 0.0) AS coverage_pct, "
        "       collect(DISTINCT {via: changed_qn, depth: depth, "
        "                         edges: edge_provenance}) AS reached_via "
        "ORDER BY qualified_name "
        "LIMIT $limit"
    )


def change_impact(
    backend: GraphBackend, project: str, diff: str,
    max_depth: int = 5, provenance: str = "any", limit: int = 200,
) -> dict[str, Any]:
    """Given a unified diff, return changed/impacted symbols and tests to run."""
    max_depth = max(_MAX_DEPTH_MIN, min(_MAX_DEPTH_MAX, int(max_depth)))
    if provenance not in ("any", "static", "runtime"):
        provenance = "any"

    parsed = parse_diff(diff)
    if not parsed:
        return _empty_result(changed_files=0)

    files_spec = [
        {"path": path, "lines": sorted(lines)}
        for path, lines in sorted(parsed.items())
    ]

    changed_rows = backend.execute(
        _CHANGE_IMPACT_QUERY_A, project=project, files=files_spec,
    )
    changed = [_change_symbol_from_row(r) for r in changed_rows]
    changed_qns = [c["qualified_name"] for c in changed]
    files_in_changed = {c["file"] for c in changed}
    unmatched_files = sorted(set(parsed.keys()) - files_in_changed)

    impacted_rows: list[dict[str, Any]] = []
    if changed_qns:
        impacted_rows = backend.execute(
            _query_b_cypher(max_depth),
            project=project, changed_qns=changed_qns,
            provenance=provenance, limit=limit,
        )
    impacted = [_impacted_from_row(r) for r in impacted_rows]

    all_affected_qns = sorted({
        *changed_qns,
        *(i["qualified_name"] for i in impacted),
    })
    test_rows: list[dict[str, Any]] = []
    if all_affected_qns:
        test_rows = backend.execute(
            _CHANGE_IMPACT_QUERY_C, project=project,
            all_affected_qns=all_affected_qns,
        )
    tests_to_run = [_test_from_row(r) for r in test_rows]

    max_depth_reached = max(
        (via["depth"] for sym in impacted for via in sym["reached_via"]),
        default=0,
    )

    return {
        "changed": changed,
        "impacted": impacted,
        "tests_to_run": tests_to_run,
        "unmatched_files": unmatched_files,
        "stats": {
            "changed_files": len(parsed),
            "changed_symbols": len(changed),
            "impacted_symbols": len(impacted),
            "tests_to_run": len(tests_to_run),
            "max_depth_reached": max_depth_reached,
        },
    }


def _empty_result(changed_files: int) -> dict[str, Any]:
    return {
        "changed": [],
        "impacted": [],
        "tests_to_run": [],
        "unmatched_files": [],
        "stats": {
            "changed_files": changed_files,
            "changed_symbols": 0,
            "impacted_symbols": 0,
            "tests_to_run": 0,
            "max_depth_reached": 0,
        },
    }


def _change_symbol_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_symbol_from_row(row),
        "runtime_observed": bool(row.get("runtime_observed")),
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
    }


def _impacted_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_symbol_from_row(row),
        "runtime_observed": bool(row.get("runtime_observed")),
        "coverage_pct": float(row.get("coverage_pct") or 0.0),
        "reached_via": [
            {
                "via": entry["via"],
                "depth": int(entry["depth"]),
                "edges": [
                    {"static": bool(e.get("static")),
                     "runtime": bool(e.get("runtime"))}
                    for e in entry.get("edges") or []
                ],
            }
            for entry in row.get("reached_via") or []
        ],
    }


def _test_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_symbol_from_row(row),
        "test_outcome": row.get("test_outcome") or "",
        "covers_symbols": sorted(row.get("covers_symbols") or []),
        "avg_coverage_pct": float(row.get("avg_coverage_pct") or 0.0),
    }


# -- run_cypher -------------------------------------------------------

# Class-name fragments used to categorize neo4j driver exceptions
# without importing the driver at module load time.
_SYNTAX_ERROR_NAMES = {"CypherSyntaxError", "InvalidInput"}
_WRITE_ERROR_CODES = (
    "Neo.ClientError.Statement.AccessMode",
    "Neo.ClientError.Statement.SemanticError",
)
_TIMEOUT_NAME_FRAGMENTS = ("Timeout", "TimedOut")
_TIMEOUT_CODE_FRAGMENTS = ("TransactionTimedOut", "Timeout")


def _categorize_backend_error(exc: Exception, query: str,
                              timeout_seconds: int) -> Exception:
    """Map a backend exception to a typed cypher_guard error.

    Already-typed cypher_guard errors are passed through unchanged.
    """
    # Pass-through for our own typed errors.
    if isinstance(exc, (ForbiddenKeywordError, CypherSyntaxError,
                        CypherTimeoutError, EngineWriteAttemptedError)):
        return exc

    name = type(exc).__name__
    message = str(exc)
    code = getattr(exc, "code", "") or ""

    if (any(t in name for t in _TIMEOUT_NAME_FRAGMENTS)
            or any(t in code for t in _TIMEOUT_CODE_FRAGMENTS)
            or "timed out" in message.lower()
            or "transaction has been terminated" in message.lower()):
        return CypherTimeoutError(timeout_seconds, query)
    if name in _SYNTAX_ERROR_NAMES or "SyntaxError" in name:
        return CypherSyntaxError(message, query)
    if code in _WRITE_ERROR_CODES or "writes" in message.lower():
        return EngineWriteAttemptedError(query)
    return exc


def run_cypher(
    backend: GraphBackend, project: str, query: str,
    params: dict[str, Any] | None = None,
    row_limit: int = 1000, timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run a read-only Cypher query for an agent.

    Pipeline: lexical pre-scan -> $project injection -> auto-LIMIT ->
    READ transaction -> truncate -> return ``{rows, truncated, row_count,
    summary}``. Each failure surfaces as a typed ``CypherError`` subclass.
    """
    kw = forbidden_keyword(query)
    if kw is not None:
        raise ForbiddenKeywordError(kw, query)

    final_params = inject_project(params, project)
    # Fetch row_limit + 1 so we can tell whether more rows existed.
    final_query = auto_limit(query, row_limit + 1)

    try:
        records, summary = backend.execute_read(
            final_query, timeout_seconds=timeout_seconds, **final_params,
        )
    except Exception as exc:
        raise _categorize_backend_error(exc, final_query, timeout_seconds) \
            from exc

    truncated = len(records) > row_limit
    if truncated:
        records = records[:row_limit]

    return {
        "rows": records,
        "truncated": truncated,
        "row_count": len(records),
        "summary": summary,
    }


# -- describe_schema --------------------------------------------------

_NEO4J_VERSION_HINT = "5.26+"

_NODE_LABELS_DESCRIPTION: dict[str, dict[str, Any]] = {
    "Project":  {"key": "name",
                 "properties": ["name", "root_path"]},
    "File":     {"key": "path",
                 "properties": ["path", "name", "language",
                                "parse_error", "content_hash"]},
    "Class":    {"key": "qualified_name",
                 "properties": ["qualified_name", "name", "file",
                                "start_line", "end_line",
                                "decorators", "source"]},
    "Function": {"key": "qualified_name",
                 "properties": ["qualified_name", "name", "file",
                                "start_line", "end_line",
                                "decorators", "source",
                                "runtime_observed", "coverage_pct",
                                "runtime_stale",
                                "test_outcome", "test_duration"]},
    "Method":   {"key": "qualified_name",
                 "properties": ["qualified_name", "name", "file",
                                "start_line", "end_line",
                                "decorators", "source",
                                "runtime_observed", "coverage_pct",
                                "runtime_stale"]},
    "Test":     {"note": ("An additional label on Function nodes "
                          "(test functions covered by livegraph trace). "
                          "Test nodes also satisfy :Function.")},
    "Module":   {"key": "name",
                 "properties": ["name", "kind"]},
}

_EDGE_TYPES_DESCRIPTION: dict[str, dict[str, Any]] = {
    "CONTAINS":   {"from": "Project|File", "to": "File",
                   "properties": []},
    "DEFINES":    {"from": "File", "to": "Class|Function",
                   "properties": []},
    "HAS_METHOD": {"from": "Class", "to": "Method", "properties": []},
    "IMPORTS":    {"from": "File", "to": "File|Module",
                   "properties": ["raw", "line"]},
    "CALLS":      {"from": "Function|Method", "to": "Function|Method",
                   "properties": ["static", "runtime",
                                  "observed_count", "call_site_lines"],
                   "note": ("Provenance flags: c.static=true means AST "
                            "predicted the call; c.runtime=true means it "
                            "was observed executing. "
                            "(static=false, runtime=true) is the "
                            "dynamic-dispatch differentiator.")},
    "COVERS":     {"from": "Test", "to": "Function|Method",
                   "properties": ["lines_covered", "lines_total",
                                  "coverage_pct"]},
}

_SAFETY_DESCRIPTION: dict[str, Any] = {
    "read_only": True,
    "forbidden_keywords": ["CREATE", "MERGE", "DELETE", "DETACH DELETE",
                           "SET", "REMOVE", "DROP", "LOAD CSV",
                           "USING PERIODIC COMMIT", "CALL"],
    "row_limit_default": 1000,
    "timeout_seconds_default": 30,
    "project_auto_injected": True,
    "convention": ("Every query should scope through "
                   "(:Project {name: $project})-[:CONTAINS]->(:File)->... ; "
                   "the $project parameter is injected automatically."),
}

_EXAMPLE_QUERIES: list[dict[str, Any]] = [
    {
        "intent": "Find a symbol by name",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(s) "
            "WHERE toLower(s.name) CONTAINS toLower($q) "
            "RETURN s.qualified_name, s.name, labels(s), "
            "       s.file, s.start_line "
            "LIMIT 20"
        ),
        "params_hint": {"q": "<search term>"},
    },
    {
        "intent": "Find who calls a symbol",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(callee) "
            "WHERE callee.qualified_name = $qn "
            "MATCH (caller)-[c:CALLS]->(callee) "
            "RETURN caller.qualified_name, c.static, c.runtime, "
            "       c.observed_count"
        ),
        "params_hint": {"qn": "<qualified_name>"},
    },
    {
        "intent": ("Dynamic-dispatch calls — runtime caught what static "
                   "missed"),
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(caller)"
            "-[c:CALLS]->(callee) "
            "WHERE c.runtime = true "
            "  AND coalesce(c.static, false) = false "
            "RETURN caller.qualified_name, callee.qualified_name, "
            "       c.observed_count "
            "LIMIT 50"
        ),
        "params_hint": {},
    },
    {
        "intent": "Tests that cover a symbol",
        "query": (
            "MATCH (s {qualified_name: $qn}) "
            "MATCH (t:Test)-[c:COVERS]->(s) "
            "RETURN t.qualified_name, c.coverage_pct, "
            "       c.lines_covered, c.lines_total "
            "ORDER BY c.coverage_pct DESC"
        ),
        "params_hint": {"qn": "<qualified_name>"},
    },
    {
        "intent": "Untested functions/methods",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
            "-[:DEFINES|HAS_METHOD*1..2]->(s) "
            "WHERE (s:Function OR s:Method) AND NOT s:Test "
            "  AND coalesce(s.runtime_observed, false) = false "
            "RETURN s.qualified_name, s.file "
            "LIMIT 100"
        ),
        "params_hint": {},
    },
    {
        "intent": "Files that import a given file",
        "query": (
            "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
            "-[r:IMPORTS]->(dst:File {path: $file}) "
            "RETURN src.path, r.raw, r.line "
            "ORDER BY src.path"
        ),
        "params_hint": {"file": "<relative path>"},
    },
]


def describe_schema(backend: GraphBackend,
                    project: str) -> dict[str, Any]:
    """Return the static schema description for the configured project.

    No backend reads — every field is statically derived from the
    livegraph schema. The agent caches the response per session.
    """
    _ = backend  # signature consistency with the rest of the tools module
    return {
        "project": project,
        "neo4j_version": _NEO4J_VERSION_HINT,
        "node_labels": _NODE_LABELS_DESCRIPTION,
        "edge_types": _EDGE_TYPES_DESCRIPTION,
        "safety": _SAFETY_DESCRIPTION,
        "example_queries": _EXAMPLE_QUERIES,
    }


# -- semantic_search --------------------------------------------------

_INDEX_EXISTS_CYPHER = (
    "SHOW INDEXES YIELD name, type "
    "WHERE name = $name AND type = 'VECTOR' "
    "RETURN name"
)


_EMBEDDED_COUNT_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s:Symbol) "
    "RETURN count(DISTINCT s) AS n"
)


_VECTOR_QUERY_CYPHER = (
    "CALL db.index.vector.queryNodes($index_name, $k_padded, $query_vector) "
    "YIELD node, score "
    "WITH node, score "
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(node) "
    "WHERE ($kind = 'any' AND (node:Function OR node:Method)) "
    "   OR ($kind = 'function' AND node:Function AND NOT node:Test) "
    "   OR ($kind = 'method' AND node:Method) "
    "RETURN node.qualified_name AS qualified_name, "
    "       node.name AS name, "
    "       head([l IN labels(node) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       node.file AS file, "
    "       node.start_line AS start_line, "
    "       node.end_line AS end_line, "
    "       coalesce(node.source, '') AS source, "
    "       score "
    "ORDER BY score DESC "
    "LIMIT $limit"
)


def _snippet(source: str, lines: int = 3) -> str:
    """First ``lines`` non-blank lines of ``source``, joined with newlines."""
    out: list[str] = []
    for raw_line in source.splitlines():
        if raw_line.strip():
            out.append(raw_line)
            if len(out) >= lines:
                break
    return "\n".join(out)


def _index_exists(backend: GraphBackend) -> bool:
    rows = backend.execute(_INDEX_EXISTS_CYPHER, name=INDEX_NAME)
    return bool(rows)


def _embedded_count(backend: GraphBackend, project: str) -> int:
    rows = backend.execute(_EMBEDDED_COUNT_CYPHER, project=project)
    if not rows:
        return 0
    return int(rows[0].get("n") or 0)


def semantic_search(
    backend: GraphBackend, project: str, provider: EmbeddingProvider,
    query: str, limit: int = 10, kind: str = "any",
) -> dict[str, Any]:
    """Find code symbols by vector similarity to ``query``."""
    if not _index_exists(backend):
        return {
            "results": [],
            "model": provider.name,
            "embedded_count": 0,
            "warning": "no embeddings yet; run `livegraph embed` first",
        }

    query_vector = provider.encode([query])[0]
    k_padded = limit + 50

    rows = backend.execute(
        _VECTOR_QUERY_CYPHER,
        index_name=INDEX_NAME, project=project,
        k_padded=k_padded, query_vector=query_vector,
        kind=kind, limit=limit,
    )

    results = [
        {
            "qualified_name": r.get("qualified_name"),
            "name": r.get("name"),
            "kind": r.get("kind"),
            "file": r.get("file"),
            "start_line": r.get("start_line"),
            "end_line": r.get("end_line"),
            "score": float(r.get("score") or 0.0),
            "snippet": _snippet(r.get("source") or ""),
        }
        for r in rows
        if r.get("qualified_name") is not None
    ]

    return {
        "results": results,
        "model": provider.name,
        "embedded_count": _embedded_count(backend, project),
        "warning": None,
    }
