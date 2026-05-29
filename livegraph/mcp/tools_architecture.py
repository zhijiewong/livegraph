"""MCP tools that analyze project architecture (Phase 11).

Three tools: ``find_cycles``, ``layering_violations``, ``hubs``.
All read-only; no new node or edge types are introduced. This file
will grow as Tasks 3 and 4 append the other two tools.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.mcp._tarjan import strongly_connected_components

_VALID_KINDS = ("any", "function", "method")
_VALID_PROVENANCE = ("any", "static", "runtime")
_VALID_SCOPES = ("call", "module")
_VALID_EDGE_KINDS = ("any", "imports", "calls")

_MAX_LIMIT = 100
_MAX_VIOLATIONS = 200
_MAX_MIN_FANIN = 1000
_MAX_MIN_SIZE = 100


# ---- find_cycles ----------------------------------------------------

_CALL_EDGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(src) "
    "WHERE src:Function OR src:Method "
    "MATCH (src)-[c:CALLS]->(tgt) "
    "WHERE ($provenance = 'any' "
    "    OR ($provenance = 'static' AND c.static = true) "
    "    OR ($provenance = 'runtime' AND c.runtime = true)) "
    "RETURN src.qualified_name AS source, "
    "       tgt.qualified_name AS target"
)

_MODULE_EDGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[:IMPORTS]->(tgt:File) "
    "RETURN src.path AS source, tgt.path AS target"
)


def find_cycles(
    backend: GraphBackend,
    project: str,
    scope: str = "call",
    provenance: str = "any",
    min_size: int = 2,
    limit: int = 20,
) -> dict[str, Any]:
    """Return strongly-connected components in the call or import graph."""
    if scope not in _VALID_SCOPES:
        return {
            "scope": scope, "provenance": provenance,
            "cycles": [],
            "warning": (
                f"invalid scope {scope!r}; "
                f"must be one of {list(_VALID_SCOPES)}"
            ),
        }
    if scope == "call" and provenance not in _VALID_PROVENANCE:
        return {
            "scope": scope, "provenance": provenance,
            "cycles": [],
            "warning": (
                f"invalid provenance {provenance!r}; "
                f"must be one of {list(_VALID_PROVENANCE)}"
            ),
        }
    min_size = max(1, min(int(min_size), _MAX_MIN_SIZE))
    limit = max(1, min(int(limit), _MAX_LIMIT))

    if scope == "module":
        rows = backend.execute(_MODULE_EDGES_CYPHER, project=project)
    else:
        rows = backend.execute(
            _CALL_EDGES_CYPHER, project=project, provenance=provenance,
        )

    graph: dict[str, list[str]] = {}
    for row in rows:
        s = row.get("source")
        t = row.get("target")
        if s is None or t is None:
            continue
        graph.setdefault(s, []).append(t)
        graph.setdefault(t, [])

    sccs = strongly_connected_components(graph)

    cycles: list[dict[str, Any]] = []
    for component in sccs:
        if len(component) < min_size:
            continue
        # Singleton with no self-loop is acyclic; filter unless min_size=1
        # AND there's a self-loop.
        if len(component) == 1:
            node = component[0]
            if node not in graph.get(node, []):
                continue
        cycles.append({
            "size": len(component),
            "nodes": sorted(component),
        })

    cycles.sort(key=lambda c: (-c["size"], c["nodes"][0]))
    cycles = cycles[:limit]

    warning = None
    if not cycles and not graph:
        warning = (
            "no project data; run `livegraph build` to ingest the project"
        )

    return {
        "scope": scope, "provenance": provenance,
        "cycles": cycles, "warning": warning,
    }


# ---- layering_violations -------------------------------------------

_ALL_FILES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(f:File) "
    "RETURN f.path AS path"
)

_IMPORTS_EDGES_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(src:File)"
    "-[:IMPORTS]->(tgt:File) "
    "RETURN src.path AS from_file, tgt.path AS to_file"
)

_CALL_EDGES_BY_FILE_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(sf:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "MATCH (s)-[:CALLS]->(t) "
    "MATCH (tf:File)-[:DEFINES|HAS_METHOD*1..2]->(t) "
    "WHERE sf <> tf "
    "RETURN DISTINCT sf.path AS from_file, tf.path AS to_file"
)


def _assign_layer(
    path: str, layers: list[dict[str, Any]],
) -> str | None:
    import fnmatch
    for layer in layers:
        name = layer.get("name")
        patterns = layer.get("patterns") or []
        for pat in patterns:
            if fnmatch.fnmatch(path, pat):
                return name
    return None


def layering_violations(
    backend: GraphBackend,
    project: str,
    layers: list[dict[str, Any]],
    edge_kind: str = "any",
    limit: int = 50,
) -> dict[str, Any]:
    """Report edges that go 'up' the supplied layering."""
    if not layers:
        return {
            "violations": [],
            "summary": {"violations": 0, "files_unlayered": 0,
                        "edges_checked": 0},
            "warning": "layers definition is empty",
        }
    if edge_kind not in _VALID_EDGE_KINDS:
        return {
            "violations": [],
            "summary": {"violations": 0, "files_unlayered": 0,
                        "edges_checked": 0},
            "warning": (
                f"invalid edge_kind {edge_kind!r}; "
                f"must be one of {list(_VALID_EDGE_KINDS)}"
            ),
        }
    limit = max(1, min(int(limit), _MAX_VIOLATIONS))

    # Layer rank: lower index = "upper" layer. Edge from rank_a to
    # rank_b is a violation iff rank_a > rank_b (going up).
    rank: dict[str, int] = {
        layer["name"]: i for i, layer in enumerate(layers)
    }

    # 1) File -> layer assignment.
    file_rows = backend.execute(_ALL_FILES_CYPHER, project=project)
    file_layer: dict[str, str] = {}
    unlayered = 0
    for row in file_rows:
        path = row.get("path")
        if not path:
            continue
        layer = _assign_layer(path, layers)
        if layer is None:
            unlayered += 1
        else:
            file_layer[path] = layer

    # 2) Edge fetch.
    edge_rows: list[tuple[str | None, str | None, str]] = []
    if edge_kind in ("any", "imports"):
        for row in backend.execute(_IMPORTS_EDGES_CYPHER, project=project):
            edge_rows.append(
                (row.get("from_file"), row.get("to_file"), "imports"),
            )
    if edge_kind in ("any", "calls"):
        for row in backend.execute(_CALL_EDGES_BY_FILE_CYPHER,
                                   project=project):
            edge_rows.append(
                (row.get("from_file"), row.get("to_file"), "calls"),
            )

    violations: list[dict[str, Any]] = []
    edges_checked = 0
    for from_f, to_f, kind in edge_rows:
        if not from_f or not to_f:
            continue
        from_layer = file_layer.get(from_f)
        to_layer = file_layer.get(to_f)
        if from_layer is None or to_layer is None:
            continue
        edges_checked += 1
        if from_layer == to_layer:
            continue
        if rank[from_layer] > rank[to_layer]:
            violations.append({
                "from_file": from_f,
                "to_file": to_f,
                "from_layer": from_layer,
                "to_layer": to_layer,
                "edge_kind": kind,
            })

    violations.sort(
        key=lambda v: (v["from_layer"], v["from_file"],
                       v["to_file"], v["edge_kind"]),
    )
    violations = violations[:limit]

    return {
        "violations": violations,
        "summary": {
            "violations": len(violations),
            "files_unlayered": unlayered,
            "edges_checked": edges_checked,
        },
        "warning": None,
    }


# ---- hubs ----------------------------------------------------------

_HUBS_CYPHER = (
    "MATCH (:Project {name: $project})-[:CONTAINS]->(:File)"
    "-[:DEFINES|HAS_METHOD*1..2]->(s) "
    "WHERE ($kind = 'any' AND (s:Function OR s:Method)) "
    "   OR ($kind = 'function' AND s:Function AND NOT s:Test) "
    "   OR ($kind = 'method' AND s:Method) "
    "OPTIONAL MATCH (caller)-[:CALLS]->(s) "
    "OPTIONAL MATCH (s)-[:CALLS]->(callee) "
    "WITH s, "
    "     count(DISTINCT caller) AS in_callers, "
    "     count(DISTINCT callee) AS out_callees "
    "WHERE in_callers >= $min_fanin "
    "RETURN s.qualified_name AS qualified_name, "
    "       head([l IN labels(s) "
    "             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind, "
    "       s.file AS file, "
    "       in_callers, out_callees "
    "ORDER BY in_callers DESC, qualified_name ASC "
    "LIMIT $limit"
)


def hubs(
    backend: GraphBackend,
    project: str,
    kind: str = "any",
    min_fanin: int = 10,
    limit: int = 20,
) -> dict[str, Any]:
    """Symbols with high in-degree (most-called functions/methods)."""
    if kind not in _VALID_KINDS:
        return {
            "results": [],
            "warning": (
                f"invalid kind {kind!r}; "
                f"must be one of {list(_VALID_KINDS)}"
            ),
        }
    min_fanin = max(1, min(int(min_fanin), _MAX_MIN_FANIN))
    limit = max(1, min(int(limit), _MAX_LIMIT))
    rows = backend.execute(
        _HUBS_CYPHER, project=project, kind=kind,
        min_fanin=min_fanin, limit=limit,
    )
    return {"results": rows, "warning": None}
